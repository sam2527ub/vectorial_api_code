"""Chunked async job: partial S3 checkpoints for large ground-truth extraction runs."""

from __future__ import annotations

import copy
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from app import database
from app.config import logger, s3_bucket, s3_client
from app.linkedin_tiered_s3 import get_linkedin_tiered_s3_artifact_names
from app.utils.s3_utils import get_audience_type_from_source, get_s3_key_for_audience, try_fetch_json_from_s3, upload_json_to_s3

from app.services.linkedin_stimulus_extraction_service.stimulus_data import load_allowed_categories_from_mapping
from app.services.linkedin_room_pipeline_async.linkedin_room_pipeline_job_repository import (
    JOB_TYPE_GROUND_TRUTH,
    get_linkedin_room_pipeline_job,
    update_linkedin_room_pipeline_job,
)
from app.services.linkedin_room_pipeline_async.workflow_orchestration import (
    should_self_trigger_next_chunk,
)

from .errors import GroundTruthExtractionError
from .ground_truth_extraction_config import get_ground_truth_extraction_config
from .ground_truth_extraction_core import (
    classification_done,
    has_successful_topic_labels,
    iter_mutable_posts,
    mark_missing_body_on_post,
    mark_unknown_category_on_post,
    normalize_contextual_payload,
)
from .ground_truth_extraction_processor import (
    collect_pending_ground_truth_posts,
    run_ground_truth_extraction_on_post_list,
    topics_mapping_from_mapping_object,
)
from .ground_truth_extraction_room_runner import _resolve_tier2_mapping


def _apply_terminal_marks(working: Dict[str, Any], topics: Dict[str, List[str]]) -> None:
    w = normalize_contextual_payload(working)
    for _uid, post in iter_mutable_posts(w):
        if classification_done(post):
            continue
        if not (post.get("body_text") or "").strip():
            mark_missing_body_on_post(post)
            continue
        cat = (post.get("category") or "").strip()
        if not topics.get(cat):
            mark_unknown_category_on_post(post)


def _partial_s3_path(base: str, final_artifact: str) -> str:
    return f"{base}/{final_artifact}.partial"


# Fields copied from a previous ground-truth JSON onto the current stimulus (same post_id / profile).
_GT_LABEL_KEYS = (
    "topics_ordered",
    "topic_probabilities",
    "topic_probabilities_before_normalisation",
    "topic_logprobs",
    "predicted_themes",
    "num_topics_classified",
    "classification_method",
    "prediction_uses_post_body",
    "per_topic_errors",
    "topic_classification_error",
)


def _merge_ground_truth_from_final(working: Dict[str, Any], final_doc: Dict[str, Any]) -> int:
    """Copy per-post labels from ``final_doc`` onto matching posts in ``working``. Returns posts updated."""
    w = normalize_contextual_payload(working)
    src = normalize_contextual_payload(final_doc)
    w_by_user = w.get("results_by_user")
    s_by_user = src.get("results_by_user")
    if not isinstance(w_by_user, dict) or not isinstance(s_by_user, dict):
        return 0
    n = 0
    for uid, w_bundle in w_by_user.items():
        if not isinstance(w_bundle, dict):
            continue
        s_bundle = s_by_user.get(uid)
        if not isinstance(s_bundle, dict):
            continue
        w_posts = w_bundle.get("posts") or []
        s_index = {
            p.get("post_id"): p
            for p in (s_bundle.get("posts") or [])
            if isinstance(p, dict) and p.get("post_id")
        }
        for t_post in w_posts:
            if not isinstance(t_post, dict):
                continue
            sp = s_index.get(t_post.get("post_id"))
            if not isinstance(sp, dict) or not classification_done(sp):
                continue
            for k in _GT_LABEL_KEYS:
                if k in sp and sp.get(k) is not None:
                    t_post[k] = copy.deepcopy(sp[k])
            t_post.pop("topic_yes_probability", None)
            t_post.pop("topic_yes_probability_softmax", None)
            t_post.pop("cluster", None)
            n += 1
    return n


def _get_or_init_working(
    *,
    base: str,
    stimulus_key: str,
    partial_artifact: str,
    topics: Dict[str, List[str]],
) -> Dict[str, Any]:
    pkey = _partial_s3_path(base, partial_artifact)
    partial = try_fetch_json_from_s3(pkey)
    if partial and isinstance(partial, dict) and partial.get("results_by_user"):
        return copy.deepcopy(partial)
    st = try_fetch_json_from_s3(f"{base}/{stimulus_key}")
    if not st or not isinstance(st, dict):
        raise GroundTruthExtractionError(
            f"Missing S3 object {stimulus_key!r} (stimulus must exist before ground-truth extraction)"
        )
    w = copy.deepcopy(st)
    # A completed run deletes .partial; without merging, only stimulus loads and every post
    # looks unlabeled. Merge from prior final ground_truth_*.json so re-runs skip done posts.
    final_key = f"{base.rstrip('/')}/{partial_artifact.lstrip('/')}"
    final_prev = try_fetch_json_from_s3(final_key)
    if final_prev and isinstance(final_prev, dict) and final_prev.get("results_by_user"):
        merged = _merge_ground_truth_from_final(w, final_prev)
        if merged:
            logger.info(
                "[GTE] Merged %s post label(s) from existing %s (idempotent re-run)",
                merged,
                partial_artifact,
            )
    _apply_terminal_marks(w, topics)
    return w


def _update_manifest(base: str, man_key: str, is_t2: bool, s3_url: str) -> None:
    man: Dict[str, Any] = try_fetch_json_from_s3(f"{base}/{man_key}") or {}
    if not isinstance(man, dict):
        man = {}
    if is_t2:
        man["ground_truth_extraction_tier2_s3_url"] = s3_url
        man["ground_truth_extraction_tier2_at"] = datetime.now(timezone.utc).isoformat()
    else:
        man["ground_truth_extraction_tier1_s3_url"] = s3_url
        man["ground_truth_extraction_tier1_at"] = datetime.now(timezone.utc).isoformat()
    upload_json_to_s3(
        f"{base}/{man_key}", {k: v for k, v in man.items() if k != "manifest_s3_url"}
    )


def _save_partial(
    base: str, final_artifact: str, working: Dict[str, Any], enabled: bool
) -> None:
    if not enabled:
        return
    pkey = _partial_s3_path(base, final_artifact)
    upload_json_to_s3(pkey, normalize_contextual_payload(working))


def _count_successful(working: Dict[str, Any]) -> int:
    w = normalize_contextual_payload(working)
    n = 0
    for _u, p in iter_mutable_posts(w):
        if has_successful_topic_labels(p):
            n += 1
    return n


def _delete_partial(base: str, final_artifact: str) -> None:
    from app.config import s3_client as sc, s3_bucket as bkt
    if not sc or not bkt:
        return
    pkey = _partial_s3_path(base, final_artifact)
    try:
        sc.delete_object(Bucket=bkt, Key=pkey)
    except Exception:
        pass


async def _maybe_trigger_next_chunk(
    *,
    base_url: str,
    audience_room_id: str,
    job_id: str,
    enterprise_name: Optional[str],
    model: Optional[str],
    workflow_orchestrated: bool,
) -> None:
    if should_self_trigger_next_chunk(workflow_orchestrated=workflow_orchestrated):
        await _next_trigger(base_url, audience_room_id, job_id, enterprise_name, model)


async def _next_trigger(
    base_url: str,
    audience_room_id: str,
    job_id: str,
    enterprise_name: Optional[str],
    model: Optional[str],
) -> None:
    url = (
        f"{base_url.rstrip('/')}/api/v1/audience-rooms/{audience_room_id}/"
        "ground-truth-extraction/async/process"
    )
    params: Dict[str, Any] = {"jobId": job_id}
    if enterprise_name is not None:
        params["enterpriseName"] = enterprise_name
    if model is not None:
        params["model"] = model
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            await c.post(url, params=params)
    except Exception as e:
        logger.info("[GTE] next chunk trigger: %s", e)


async def run_ground_truth_extraction_process_chunk(
    *,
    job_id: str,
    audience_room_id: str,
    enterprise_name: Optional[str],
    model: Optional[str],
    base_url: str,
    workflow_orchestrated: bool = False,
) -> None:
    if not s3_client or not s3_bucket:
        update_linkedin_room_pipeline_job(
            job_id, enterprise_name=enterprise_name, status="FAILED", error="S3 not configured"
        )
        return

    job = get_linkedin_room_pipeline_job(job_id, enterprise_name=enterprise_name)
    if not job or job.get("audience_room_id") != audience_room_id:
        return
    if job.get("job_type") != JOB_TYPE_GROUND_TRUTH:
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
    tier_mode = str(jr.get("tier_mode") or "both").strip().lower()
    if tier_mode not in ("both", "tier1", "tier2"):
        tier_mode = "both"

    art = get_linkedin_tiered_s3_artifact_names()
    base = get_s3_key_for_audience(
        audience_room_id, art.tiered_folder, enterprise_name, room.source
    ).rstrip("/")

    map_t1 = try_fetch_json_from_s3(f"{base}/{art.discovered_category_topic_mapping_tier1_filtered}")
    if not map_t1 or not isinstance(map_t1, dict):
        update_linkedin_room_pipeline_job(
            job_id, enterprise_name=enterprise_name, status="FAILED", error="Missing mapping tier1 on S3"
        )
        return
    try:
        load_allowed_categories_from_mapping(map_t1)
    except Exception:
        pass
    topics_t1 = topics_mapping_from_mapping_object(map_t1)
    map_t2o = try_fetch_json_from_s3(f"{base}/{art.discovered_category_topic_mapping_tier2_filtered}")
    topics_t2 = topics_mapping_from_mapping_object(
        _resolve_tier2_mapping(map_t2o if isinstance(map_t2o, dict) else None, map_t1)
    )

    cfg = get_ground_truth_extraction_config()
    bpc = max(1, cfg.batch.posts_per_process_invocation)
    mdl = model or job.get("model") or os.environ.get("OPENAI_MODEL", cfg.default_model)
    save_partial = cfg.batch.save_partial_after_each_chunk

    current_tier: str
    if tier_mode == "tier2":
        current_tier = "tier2"
    elif tier_mode == "tier1":
        current_tier = "tier1"
    else:
        current_tier = str(job.get("current_tier") or "tier1")
    is_t2 = current_tier == "tier2"
    tmap = topics_t2 if is_t2 else topics_t1
    stimulus_key = (
        art.contextual_stimulus_extraction_tier2_filtered
        if is_t2
        else art.contextual_stimulus_extraction_tier1_filtered
    )
    out_key: str = (
        art.ground_truth_extraction_tier2_filtered
        if is_t2
        else art.ground_truth_extraction_tier1_filtered
    )

    working: Any = None
    try:
        working = _get_or_init_working(
            base=base, stimulus_key=stimulus_key, partial_artifact=out_key, topics=tmap
        )
        n_pending_before = len(collect_pending_ground_truth_posts(working, tmap))
        if int(job.get("total_items") or 0) == 0 and n_pending_before > 0:
            update_linkedin_room_pipeline_job(
                job_id, enterprise_name=enterprise_name, total_items=n_pending_before, error=None
            )

        if n_pending_before == 0:
            w = normalize_contextual_payload(working)
            url = upload_json_to_s3(f"{base}/{out_key}", w)
            _update_manifest(base, art.manifest, is_t2, url)
            _delete_partial(base, out_key)
            if tier_mode == "both" and not is_t2:
                update_linkedin_room_pipeline_job(
                    job_id,
                    enterprise_name=enterprise_name,
                    current_tier="tier2",
                    next_batch_index=0,
                    status="PROCESSING",
                )
                await _maybe_trigger_next_chunk(
                    base_url=base_url,
                    audience_room_id=audience_room_id,
                    job_id=job_id,
                    enterprise_name=enterprise_name,
                    model=model,
                    workflow_orchestrated=workflow_orchestrated,
                )
                return
            ukey = "ground_truth_tier2_s3_url" if is_t2 else "ground_truth_tier1_s3_url"
            done_result: Dict[str, Any] = {
                "status": "ok",
                "audience_room_id": audience_room_id,
                ukey: url,
                "tier_mode": tier_mode,
                "config": cfg.to_public_dict(),
            }
            if tier_mode == "both" and is_t2:
                done_result["phases"] = "tier1_and_tier2"
            update_linkedin_room_pipeline_job(
                job_id, enterprise_name=enterprise_name, status="COMPLETED", result=done_result
            )
            return

        pending = collect_pending_ground_truth_posts(working, tmap)
        total = len(pending)
        if total == 0:
            w = normalize_contextual_payload(working)
            url = upload_json_to_s3(f"{base}/{out_key}", w)
            _update_manifest(base, art.manifest, is_t2, url)
            _delete_partial(base, out_key)
            update_linkedin_room_pipeline_job(
                job_id,
                enterprise_name=enterprise_name,
                status="COMPLETED",
                result={
                    "status": "ok",
                    "tier_mode": tier_mode,
                    "tier": current_tier,
                    "s3_key": f"{base}/{out_key}",
                    "s3_url": url,
                    "config": cfg.to_public_dict(),
                },
            )
            return

        # Pending list shrinks as posts are labeled; always take the next ``bpc`` remaining posts.
        batch = pending[:bpc] if bpc else []
        update_linkedin_room_pipeline_job(
            job_id, enterprise_name=enterprise_name, status="PROCESSING", error=None
        )
        if batch:
            await run_ground_truth_extraction_on_post_list(
                working, tmap, batch, mdl, is_tier2=is_t2, config=cfg
            )
        _save_partial(base, out_key, working, save_partial)

        n_after = len(collect_pending_ground_truth_posts(working, tmap))
        tier_done = n_after == 0

        if not tier_done:
            update_linkedin_room_pipeline_job(
                job_id,
                enterprise_name=enterprise_name,
                current_tier=current_tier,
                next_batch_index=0,
                completed_items=_count_successful(working),
            )
            await _maybe_trigger_next_chunk(
                base_url=base_url,
                audience_room_id=audience_room_id,
                job_id=job_id,
                enterprise_name=enterprise_name,
                model=model,
                workflow_orchestrated=workflow_orchestrated,
            )
            return

        w = normalize_contextual_payload(working)
        url = upload_json_to_s3(f"{base}/{out_key}", w)
        _update_manifest(base, art.manifest, is_t2, url)
        _delete_partial(base, out_key)
        if tier_mode == "both" and not is_t2:
            update_linkedin_room_pipeline_job(
                job_id,
                enterprise_name=enterprise_name,
                current_tier="tier2",
                next_batch_index=0,
                status="PROCESSING",
                completed_items=0,
            )
            await _maybe_trigger_next_chunk(
                base_url=base_url,
                audience_room_id=audience_room_id,
                job_id=job_id,
                enterprise_name=enterprise_name,
                model=model,
                workflow_orchestrated=workflow_orchestrated,
            )
        else:
            fin: Dict[str, Any] = {
                "status": "ok",
                "audience_room_id": audience_room_id,
                "s3_key": f"{base}/{out_key}",
                "s3_url": url,
                "tier": current_tier,
                "tier_mode": tier_mode,
                "config": cfg.to_public_dict(),
            }
            update_linkedin_room_pipeline_job(
                job_id, enterprise_name=enterprise_name, status="COMPLETED", result=fin
            )
    except Exception as e:
        logger.error("[GTE] chunk failed: %s", e, exc_info=True)
        try:
            if save_partial and isinstance(working, dict) and out_key:
                _save_partial(base, out_key, working, True)
        except Exception:
            pass
        update_linkedin_room_pipeline_job(
            job_id, enterprise_name=enterprise_name, status="FAILED", error=str(e)
        )
