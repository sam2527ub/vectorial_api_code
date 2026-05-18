"""Chunked async job: partial S3 checkpoints + self-trigger for large initial-prediction runs."""

from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import httpx

from app import database
from app.config import logger, s3_bucket, s3_client
from app.linkedin_tiered_s3 import get_linkedin_tiered_s3_artifact_names
from app.utils.s3_utils import (
    get_audience_type_from_source,
    get_s3_key_for_audience,
    try_fetch_json_from_s3,
    upload_json_to_s3,
)

from app.services.linkedin_room_pipeline_async.linkedin_room_pipeline_job_repository import (
    JOB_TYPE_INITIAL_PREDICTION,
    get_linkedin_room_pipeline_job,
    update_linkedin_room_pipeline_job,
)
from app.services.linkedin_room_pipeline_async.workflow_orchestration import (
    should_self_trigger_next_chunk,
)

from .initial_prediction_config import get_initial_prediction_config
from .initial_prediction_processor import run_initial_prediction_posts_batch
from .initial_prediction_s3 import (
    download_room_profiles_from_s3,
    fetch_s3_inputs_for_initial_prediction,
    partial_s3_key_for_artifact,
    profile_ids_from_contextual_payload,
    update_manifest_initial_prediction,
)
from .initial_prediction_types import (
    PIPELINE_ID,
    InitialPredictionRunParams,
    build_initial_prediction_artifact,
)

PARTIAL_VERSION = "initial_prediction_partial_v1"


def user_predictions_full_to_jsonable(
    d: Dict[str, Dict[int, Dict[str, Any]]],
) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for uid, by_idx in d.items():
        out[str(uid)] = {str(k): v for k, v in by_idx.items()}
    return out


def user_predictions_full_from_jsonable(
    raw: Any,
) -> Dict[str, Dict[int, Dict[str, Any]]]:
    out: Dict[str, Dict[int, Dict[str, Any]]] = {}
    if not isinstance(raw, dict):
        return out
    for uid, by_idx in raw.items():
        if not isinstance(by_idx, dict):
            continue
        inner: Dict[int, Dict[str, Any]] = {}
        for k, v in by_idx.items():
            try:
                ik = int(k)
            except (TypeError, ValueError):
                ik = int(str(k))
            inner[ik] = v if isinstance(v, dict) else {}
        out[str(uid)] = inner
    return out


def _slim_row_to_full_entry(row: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(row)
    stim = row.get("stimulus") or ""
    out["product_description"] = stim
    return out


def _seed_user_pred_from_final_artifact(
    final_doc: Dict[str, Any],
) -> Tuple[Dict[str, Dict[int, Dict[str, Any]]], List[Dict[str, Any]]]:
    """Rebuild nested predictions from a completed slim artifact (idempotent re-run)."""
    up: Dict[str, Dict[int, Dict[str, Any]]] = {}
    raw_up = final_doc.get("user_predictions") or {}
    for uid, rows in raw_up.items():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            ridx = row.get("review_idx")
            try:
                ri = int(ridx) if ridx is not None else 0
            except (TypeError, ValueError):
                ri = 0
            up.setdefault(str(uid), {})[ri] = _slim_row_to_full_entry(row)
    errs = list(final_doc.get("errors") or [])
    return up, errs


def _completed_keys(
    user_pred: Dict[str, Dict[int, Dict[str, Any]]],
) -> Set[Tuple[str, str]]:
    keys: Set[Tuple[str, str]] = set()
    for uid, by_idx in user_pred.items():
        for _ridx, rec in by_idx.items():
            pid = str(rec.get("post_id") or "")
            if pid:
                keys.add((str(uid), pid))
    return keys


def _pending_posts(
    all_posts: List[Tuple[str, Dict[str, Any], Dict[str, Any]]],
    done: Set[Tuple[str, str]],
) -> List[Tuple[str, Dict[str, Any], Dict[str, Any]]]:
    out: List[Tuple[str, Dict[str, Any], Dict[str, Any]]] = []
    for pid, b, post in all_posts:
        pk = (str(pid), str(post.get("post_id") or ""))
        if pk not in done:
            out.append((pid, b, post))
    return out


def _count_preds(user_pred: Dict[str, Dict[int, Dict[str, Any]]]) -> int:
    n = 0
    for _u, by_idx in user_pred.items():
        n += len(by_idx)
    return n


def _failed_sample(errors: List[Dict[str, Any]], limit: int = 32) -> List[str]:
    out: List[str] = []
    for e in errors[:limit]:
        pid = e.get("post_id")
        if pid:
            out.append(str(pid))
    return out


def _delete_partial(base: str, rel: str) -> None:
    from app.config import s3_client as sc, s3_bucket as bkt

    if not sc or not bkt:
        return
    pkey = partial_s3_key_for_artifact(base, rel)
    try:
        sc.delete_object(Bucket=bkt, Key=pkey)
    except Exception:
        pass


async def _next_trigger(
    base_url: str,
    audience_room_id: str,
    job_id: str,
    enterprise_name: Optional[str],
    model: Optional[str],
) -> None:
    url = (
        f"{base_url.rstrip('/')}/api/v1/audience-rooms/{audience_room_id}/"
        "linkedin-initial-prediction/async/process"
    )
    params_q: Dict[str, Any] = {"jobId": job_id}
    if enterprise_name is not None:
        params_q["enterpriseName"] = enterprise_name
    if model is not None:
        params_q["model"] = model
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            await c.post(url, params=params_q)
    except Exception as e:
        logger.info("[InitialPredictionChunk] next chunk trigger: %s", e)


async def run_initial_prediction_process_chunk(
    *,
    job_id: str,
    audience_room_id: str,
    enterprise_name: Optional[str],
    model: Optional[str],
    base_url: str,
    workflow_orchestrated: bool = False,
) -> None:
    from .i0.contextual_payload import coalesce_contextual_payload
    from .stimulus_posts import iter_posts_with_ground_truth

    if not s3_client or not s3_bucket:
        update_linkedin_room_pipeline_job(
            job_id, enterprise_name=enterprise_name, status="FAILED", error="S3 not configured"
        )
        return

    job = get_linkedin_room_pipeline_job(job_id, enterprise_name=enterprise_name)
    if not job or job.get("audience_room_id") != audience_room_id:
        return
    if job.get("job_type") != JOB_TYPE_INITIAL_PREDICTION:
        return
    if job.get("status") in ("COMPLETED", "FAILED"):
        return

    room = database.find_audience_room_by_id(
        audience_room_id, include_profiles=False, enterprise_name=enterprise_name
    )
    if not room:
        update_linkedin_room_pipeline_job(
            job_id, enterprise_name=enterprise_name, status="FAILED", error="Room not found"
        )
        return
    if get_audience_type_from_source(room.source) != "linkedin-audience":
        update_linkedin_room_pipeline_job(
            job_id,
            enterprise_name=enterprise_name,
            status="COMPLETED",
            result={"skipped": True, "reason": "not_linkedin_audience"},
        )
        return

    jr = job.get("result") or {}
    if not isinstance(jr, dict):
        jr = {}
    tier_n = max(1, min(2, int(jr.get("tier") or 1)))
    tier_hint = "tier1" if tier_n == 1 else "tier2"

    cfg = get_initial_prediction_config()
    bpc = max(1, int(cfg.batch.posts_per_process_invocation))
    save_partial = bool(cfg.batch.save_partial_after_each_chunk)
    mdl = model or job.get("model") or cfg.default_gen_model

    art = get_linkedin_tiered_s3_artifact_names()
    base = get_s3_key_for_audience(
        audience_room_id, art.tiered_folder, enterprise_name, room.source
    ).rstrip("/")
    rel = art.initial_prediction_tier2 if tier_n == 2 else art.initial_prediction_tier1

    user_pred: Dict[str, Dict[int, Dict[str, Any]]] = {}
    errors: List[Dict[str, Any]] = []

    partial_raw = try_fetch_json_from_s3(partial_s3_key_for_artifact(base, rel))
    if (
        isinstance(partial_raw, dict)
        and partial_raw.get("version") == PARTIAL_VERSION
        and partial_raw.get("user_predictions_full") is not None
    ):
        user_pred = user_predictions_full_from_jsonable(partial_raw["user_predictions_full"])
        errors = list(partial_raw.get("errors") or [])
    else:
        final_prev = try_fetch_json_from_s3(f"{base}/{rel}")
        if final_prev and isinstance(final_prev, dict) and final_prev.get("user_predictions"):
            user_pred, errors = _seed_user_pred_from_final_artifact(final_prev)
            logger.info(
                "[InitialPredictionChunk] seeded %s post(s) from existing %s",
                _count_preds(user_pred),
                rel,
            )

    td: Optional[tempfile.TemporaryDirectory] = None

    try:
        stim, mapping, desc = fetch_s3_inputs_for_initial_prediction(
            audience_room_id=audience_room_id,
            tier=tier_n,
            enterprise_name=enterprise_name,
            source=room.source,
        )
        working_stim = coalesce_contextual_payload(dict(stim))
        if not str(working_stim.get("source_file") or "").strip():
            working_stim["source_file"] = "contextual_stimulus.json"

        td = tempfile.TemporaryDirectory(prefix="linkedin_initial_pred_chunk_")
        root = Path(td.name)
        dp = root / "description.json"
        mp = root / "mapping.json"
        dp.write_text(json.dumps(desc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        mp.write_text(json.dumps(mapping, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        profiles_root = root / "profiles"
        ids = profile_ids_from_contextual_payload(working_stim)
        download_room_profiles_from_s3(
            audience_room_id=audience_room_id,
            profile_ids=ids,
            profiles_root=profiles_root,
            enterprise_name=enterprise_name,
            source=room.source,
        )

        all_posts = iter_posts_with_ground_truth(working_stim, tier_hint=tier_hint)
        n_total = len(all_posts)
        if int(job.get("total_items") or 0) == 0 and n_total > 0:
            update_linkedin_room_pipeline_job(
                job_id, enterprise_name=enterprise_name, total_items=n_total, error=None
            )

        done_keys = _completed_keys(user_pred)
        pending = _pending_posts(all_posts, done_keys)

        contextual_label = str(working_stim.get("source_file") or "contextual")
        desc_label = str(dp)
        mapping_label = str(mp)

        weight_text = float(cfg.weight_text_delta)
        weight_theme = float(cfg.weight_theme_delta)

        def _finalize_success() -> None:
            artifact = build_initial_prediction_artifact(
                tier=tier_n,
                gen_model=mdl,
                topic_model=mdl,
                gt_prob_threshold=cfg.gt_prob_threshold,
                weight_text=weight_text,
                weight_theme=weight_theme,
                checkpoint_every=cfg.checkpoint_every,
                contextual_label=contextual_label,
                description_label=desc_label,
                mapping_label=mapping_label,
                profiles_root=profiles_root,
                model_type_used=PIPELINE_ID,
                user_pred_by_idx=user_pred,
                errors=errors,
                extra_meta={
                    "posts_completed_successfully": _count_preds(user_pred),
                    "posts_attempted": n_total,
                    "partial_checkpoint": False,
                },
            )
            key_final = f"{base}/{rel}"
            url = upload_json_to_s3(key_final, artifact)
            update_manifest_initial_prediction(base, art.manifest, tier_n, url)
            _delete_partial(base, rel)
            update_linkedin_room_pipeline_job(
                job_id,
                enterprise_name=enterprise_name,
                status="COMPLETED",
                error=None,
                completed_items=_count_preds(user_pred),
                failed_items=len(errors),
                failed_post_ids_sample=_failed_sample(errors),
                result={
                    "status": "ok",
                    "audience_room_id": audience_room_id,
                    "tier": tier_n,
                    "s3_key": key_final,
                    "s3_url": url,
                    "config": cfg.to_public_dict(),
                },
            )

        if not pending:
            _finalize_success()
            return

        batch = pending[:bpc]
        update_linkedin_room_pipeline_job(
            job_id, enterprise_name=enterprise_name, status="PROCESSING", error=None
        )

        params = InitialPredictionRunParams(
            tier=tier_n,
            contextual_payload=working_stim,
            description_seed_path=dp,
            mapping_path=mp,
            profiles_root=profiles_root,
            output_path=None,
            mirror_repo_predictions_path=None,
            gen_model=mdl,
            topic_model=mdl,
            gt_prob_threshold=cfg.gt_prob_threshold,
            checkpoint_every=0,
            concurrent=cfg.max_concurrent_posts,
            prob_threshold=cfg.prob_threshold,
            max_retries=cfg.max_retries,
            retry_delay=cfg.retry_delay_sec,
            max_text_chars=cfg.max_text_chars,
            print_seed_context=False,
            debug_i0_prompt=False,
            no_initial_i0_prompt=True,
            s3_upload=False,
            s3_partial_upload=False,
            audience_room_id=audience_room_id,
            enterprise_name=enterprise_name,
            source=room.source,
        )

        await run_initial_prediction_posts_batch(
            params,
            all_posts_for_meta=all_posts,
            posts_to_run=batch,
            user_pred_by_idx=user_pred,
            errors=errors,
        )

        if save_partial:
            upload_json_to_s3(
                partial_s3_key_for_artifact(base, rel),
                {
                    "version": PARTIAL_VERSION,
                    "user_predictions_full": user_predictions_full_to_jsonable(user_pred),
                    "errors": errors,
                },
            )

        done_after = _completed_keys(user_pred)
        pending_after = _pending_posts(all_posts, done_after)

        update_linkedin_room_pipeline_job(
            job_id,
            enterprise_name=enterprise_name,
            completed_items=_count_preds(user_pred),
            failed_items=len(errors),
            failed_post_ids_sample=_failed_sample(errors),
        )

        if pending_after and should_self_trigger_next_chunk(
            workflow_orchestrated=workflow_orchestrated
        ):
            await _next_trigger(base_url, audience_room_id, job_id, enterprise_name, model)
            return
        if pending_after:
            return

        _finalize_success()
    except Exception as e:
        logger.error("[InitialPredictionChunk] failed: %s", e, exc_info=True)
        try:
            if save_partial and user_pred:
                upload_json_to_s3(
                    partial_s3_key_for_artifact(base, rel),
                    {
                        "version": PARTIAL_VERSION,
                        "user_predictions_full": user_predictions_full_to_jsonable(user_pred),
                        "errors": errors,
                    },
                )
        except Exception:
            pass
        update_linkedin_room_pipeline_job(
            job_id, enterprise_name=enterprise_name, status="FAILED", error=str(e)
        )
    finally:
        if td is not None:
            td.cleanup()
