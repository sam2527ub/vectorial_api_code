"""LinkedIn SGO async job worker: one outer iteration per chunk, S3 checkpoints."""

from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
import psycopg2

from .linkedin_sgo_job_health import reconcile_stale_sgo_job
from .linkedin_sgo_pipeline_async_checkpoint import (
    build_partial_resume_payload,
    build_resume_payload,
    latest_completed_checkpoint_iteration,
    latest_partial_checkpoint,
    promote_checkpoint_to_canonical_artifacts,
    restore_checkpoint_into_workdir,
    restore_partial_checkpoint_into_workdir,
    upload_iteration_checkpoint,
    upload_partial_iteration_checkpoint,
)
from .linkedin_sgo_pipeline_async_config import ensure_required_paths, get_default_namespace
from .linkedin_sgo_pipeline_async_errors import LinkedInSGOPipelineError
from app.services.linkedin_room_pipeline_async.workflow_orchestration import (
    should_self_trigger_next_chunk,
)

from .linkedin_sgo_pipeline_async_runner import run_linkedin_sgo_pipeline_async


def _mark_sgo_job_failed(
    *,
    job_id: str,
    enterprise_name: Optional[str],
    jr: Dict[str, Any],
    exc: BaseException,
    failed_posts: Optional[list] = None,
) -> None:
    from app.config import logger
    from app.services.linkedin_room_pipeline_async.linkedin_room_pipeline_job_repository import (
        update_linkedin_room_pipeline_job,
    )

    fail_result = _sgo_failed_result_meta(jr, exc, failed_posts=failed_posts)
    update_linkedin_room_pipeline_job(
        job_id,
        enterprise_name=enterprise_name,
        status="FAILED",
        error=str(exc),
        result=fail_result,
    )
    logger.error("[SGO_JOB] %s failed: %s", job_id, exc, exc_info=True)


def _sgo_failed_result_meta(
    jr: Dict[str, Any],
    exc: BaseException,
    *,
    failed_posts: Optional[list] = None,
) -> Dict[str, Any]:
    """
    Attach failure classification for operators and resume policy.

    ``sgo_resume_blocked=True``: auto-resume from S3 on ``/process`` is skipped (pipeline /
    invariant issues). ``False``: transient DB/network-class failures may resume when checkpoints exist.
    """
    base = dict(jr) if isinstance(jr, dict) else {}
    if isinstance(exc, (psycopg2.OperationalError, psycopg2.InterfaceError)):
        base["sgo_failure_class"] = "transient_db"
        base["sgo_resume_blocked"] = False
    elif isinstance(exc, LinkedInSGOPipelineError):
        base["sgo_failure_class"] = "pipeline"
        base["sgo_resume_blocked"] = True
    else:
        base["sgo_failure_class"] = "unknown"
        base["sgo_resume_blocked"] = True
    base["sgo_failed_at"] = datetime.now(timezone.utc).isoformat()
    if failed_posts:
        base["failed_posts"] = list(failed_posts)
        base["failed_posts_count"] = len(failed_posts)
    return base


def _sgo_job_progress_counts(
    num_posts: int, num_iterations_planned: int, outer_iterations_done: int
) -> tuple[int, int]:
    """
    Map outer-loop progress to total_items / completed_items.

    Each outer iteration runs the full post corpus once, so one unit of work is
    (post × outer iteration): total = posts × planned_iters, completed = posts × done_iters.
    If posts are unknown or zero, fall back to iteration counts only.
    """
    n_post = int(num_posts)
    n_it = int(num_iterations_planned)
    done = max(0, int(outer_iterations_done))
    if n_post > 0:
        return n_post * n_it, n_post * done
    return n_it, done


async def _trigger_sgo_process_endpoint(
    base_url: str,
    audience_room_id: str,
    job_id: str,
    enterprise_name: Optional[str],
    model: Optional[str],
) -> None:
    from app.config import logger

    url = (
        f"{base_url.rstrip('/')}/api/v1/audience-rooms/{audience_room_id}/"
        "linkedin-sgo-pipeline/async/process"
    )
    params: Dict[str, Any] = {"jobId": job_id}
    if enterprise_name is not None:
        params["enterpriseName"] = enterprise_name
    if model is not None:
        params["model"] = model
    try:
        from app.runtime_settings import get_runtime_settings

        timeout_s = float(get_runtime_settings().linkedin_sgo_async.process_trigger_timeout_s)
        async with httpx.AsyncClient(timeout=timeout_s) as c:
            resp = await c.post(url, params=params)
            resp.raise_for_status()
        logger.info("[SGO_JOB] Triggered process endpoint for job %s", job_id)
    except httpx.ReadTimeout:
        logger.info(
            "[SGO_JOB] Process trigger read timeout (chunk may still run) job %s",
            job_id,
        )
    except Exception as e:
        logger.error(
            "[SGO_JOB] Failed to trigger process for job %s: %s",
            job_id,
            e,
            exc_info=True,
        )


async def run_linkedin_sgo_pipeline_job_chunk(
    *,
    job_id: str,
    audience_room_id: str,
    enterprise_name: Optional[str],
    model: Optional[str],
    base_url: str,
    workflow_orchestrated: bool = False,
) -> None:
    from app.config import logger, s3_bucket, s3_client

    from app.services.linkedin_room_pipeline_async.linkedin_room_pipeline_job_repository import (
        JOB_TYPE_LINKEDIN_SGO_PIPELINE,
        get_linkedin_room_pipeline_job,
        update_linkedin_room_pipeline_job,
    )

    if not s3_client or not s3_bucket:
        update_linkedin_room_pipeline_job(
            job_id,
            enterprise_name=enterprise_name,
            status="FAILED",
            error="S3 not configured",
            result={
                "sgo_failure_class": "config",
                "sgo_resume_blocked": True,
                "sgo_failed_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return

    job = get_linkedin_room_pipeline_job(job_id, enterprise_name=enterprise_name)
    if not job or job.get("audience_room_id") != audience_room_id:
        return
    if job.get("job_type") != JOB_TYPE_LINKEDIN_SGO_PIPELINE:
        return
    if job.get("status") == "COMPLETED":
        return
    failed_resume_candidate = job.get("status") == "FAILED"
    if failed_resume_candidate:
        # May resume from S3 if checkpoints exist (handled after base_tiered is known).
        pass
    elif job.get("status") not in ("PENDING", "PROCESSING"):
        return

    from app import database
    from app.linkedin_tiered_s3 import get_linkedin_tiered_s3_artifact_names
    from app.utils.s3_utils import get_audience_type_from_source, get_s3_key_for_audience

    from .linkedin_sgo_pipeline_async_s3 import (
        download_sgo_inputs_to_workdir,
        prepare_tier2_evolution_baseline_dir_from_s3,
    )

    room = database.find_audience_room_by_id(
        audience_room_id, include_profiles=False, enterprise_name=enterprise_name
    )
    if not room:
        update_linkedin_room_pipeline_job(
            job_id,
            enterprise_name=enterprise_name,
            status="FAILED",
            error="Room not found",
            result={
                "sgo_failure_class": "invalid_room",
                "sgo_resume_blocked": True,
                "sgo_failed_at": datetime.now(timezone.utc).isoformat(),
            },
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

    from app.runtime_settings import get_runtime_settings

    stale_s, stale_meta = reconcile_stale_sgo_job(
        job,
        stale_timeout_s=float(get_runtime_settings().linkedin_sgo_async.processing_stale_timeout_s),
    )
    if stale_s and stale_meta is not None:
        update_linkedin_room_pipeline_job(
            job_id,
            enterprise_name=enterprise_name,
            status="FAILED",
            error=(
                f"Job stale in PROCESSING (no heartbeat for "
                f"{stale_meta.get('stale_processing_age_s')}s; "
                f"limit {stale_meta.get('stale_processing_timeout_s')}s)"
            ),
            result=stale_meta,
        )
        logger.warning("[SGO_JOB] %s marked FAILED (stale PROCESSING)", job_id)
        return

    num_it = max(1, int(jr.get("num_iterations") or 5))
    next_iter = max(1, int(jr.get("outer_iteration_next") or 1))
    tier_mode = str(jr.get("tier_mode") or "tier1").strip().lower()
    if tier_mode not in ("tier1", "tier2"):
        tier_mode = "tier1"
    tier_int = 2 if tier_mode == "tier2" else 1

    art = get_linkedin_tiered_s3_artifact_names()
    base_tiered = get_s3_key_for_audience(
        audience_room_id, art.tiered_folder, enterprise_name, room.source
    ).rstrip("/")

    s3_latest = latest_completed_checkpoint_iteration(
        base_tiered=base_tiered, job_id=job_id, scan_max=num_it
    )
    if failed_resume_candidate:
        if jr.get("sgo_resume_blocked") is True:
            logger.warning(
                "[SGO_OBS] event=resume_skipped_blocked job_id=%s failure_class=%s",
                job_id,
                jr.get("sgo_failure_class"),
            )
            return
        if s3_latest <= 0:
            logger.warning(
                "[SGO_JOB] job %s is FAILED with no S3 checkpoint; cannot auto-resume",
                job_id,
            )
            return
        logger.info(
            "[SGO_OBS] event=failed_job_resumed_from_checkpoint job_id=%s s3_latest_iter=%s",
            job_id,
            s3_latest,
        )
        next_iter = max(next_iter, s3_latest + 1)
        jr = {**jr, "outer_iteration_next": next_iter}
        update_linkedin_room_pipeline_job(
            job_id,
            enterprise_name=enterprise_name,
            status="PROCESSING",
            error=None,
            result=jr,
        )
        job = get_linkedin_room_pipeline_job(job_id, enterprise_name=enterprise_name) or job
        jr = job.get("result") if isinstance(job.get("result"), dict) else jr
        num_it = max(1, int(jr.get("num_iterations") or num_it))
        next_iter = max(1, int(jr.get("outer_iteration_next") or next_iter))
    elif s3_latest > 0:
        effective_next = max(next_iter, s3_latest + 1)
        if effective_next != next_iter:
            logger.info(
                "[SGO_OBS] event=reconciliation_db_behind_s3 job_id=%s db_outer_iteration_next=%s "
                "effective_next=%s s3_latest_completed_iter=%s",
                job_id,
                next_iter,
                effective_next,
                s3_latest,
            )
            next_iter = effective_next
            jr = {**jr, "outer_iteration_next": next_iter}
            try:
                update_linkedin_room_pipeline_job(
                    job_id,
                    enterprise_name=enterprise_name,
                    result=jr,
                    error=None,
                )
            except Exception:
                logger.warning(
                    "[SGO_JOB] failed to persist reconciliation for %s", job_id, exc_info=True
                )

    hb = datetime.now(timezone.utc).isoformat()
    failed_posts_acc: list = list(jr.get("failed_posts") or [])
    update_linkedin_room_pipeline_job(
        job_id,
        enterprise_name=enterprise_name,
        status="PROCESSING",
        error=None,
        result={
            **jr,
            "heartbeat_at": hb,
            "sgo_last_progress_at": hb,
            "phase": "finalize" if next_iter > num_it else "running_outer_iteration",
            "outer_iteration_running": None if next_iter > num_it else next_iter,
        },
    )

    try:
        if next_iter > num_it:
            urls = promote_checkpoint_to_canonical_artifacts(
                base_tiered=base_tiered,
                job_id=job_id,
                tier_int=tier_int,
                last_iteration_completed=num_it,
                art=art,
                manifest_key=art.manifest,
            )
            posts_done = int(jr.get("num_posts") or 0)
            total_items, completed_items = _sgo_job_progress_counts(posts_done, num_it, num_it)
            update_linkedin_room_pipeline_job(
                job_id,
                enterprise_name=enterprise_name,
                status="COMPLETED",
                completed_items=completed_items,
                total_items=total_items,
                result={
                    **jr,
                    "outer_iteration_next": next_iter,
                    "num_iterations": num_it,
                    "tier_mode": tier_mode,
                    "tier_int": tier_int,
                    "heartbeat_at": datetime.now(timezone.utc).isoformat(),
                    "s3_urls": urls,
                    "checkpoint_promoted": True,
                },
                error=None,
            )
            logger.info("[SGO_JOB] %s COMPLETED (promoted checkpoint iter=%s)", job_id, num_it)
            return

        with tempfile.TemporaryDirectory(prefix=f"linkedin_sgo_job_{job_id}_") as td:
            root = Path(td)
            work_dir = root / "work"
            profiles_root = root / "profiles"

            contextual_path, mapping_path, description_path, i0_path, profiles_root, num_posts = (
                download_sgo_inputs_to_workdir(
                    audience_room_id=audience_room_id,
                    enterprise_name=enterprise_name,
                    source=room.source,
                    tier=tier_int,
                    work_dir=work_dir,
                    profiles_root=profiles_root,
                    art=art,
                )
            )
            if num_posts <= 0:
                logger.warning(
                    "[SGO_JOB] %s: stimulus has 0 profiles; job progress uses outer-iteration counts only",
                    job_id,
                )
            total_items, completed_items = _sgo_job_progress_counts(
                num_posts, num_it, next_iter - 1
            )
            update_linkedin_room_pipeline_job(
                job_id,
                enterprise_name=enterprise_name,
                total_items=total_items,
                completed_items=completed_items,
                result={
                    **jr,
                    "num_posts": num_posts,
                    "num_iterations": num_it,
                    "tier_mode": tier_mode,
                    "tier_int": tier_int,
                    "heartbeat_at": datetime.now(timezone.utc).isoformat(),
                },
                error=None,
            )

            tier1_evo_dir = ""
            if tier_int == 2 and next_iter == 1:
                t1mirror = root / "tier1_evolution_baseline"
                prepare_tier2_evolution_baseline_dir_from_s3(
                    audience_room_id=audience_room_id,
                    enterprise_name=enterprise_name,
                    source=room.source,
                    tier1_evolution_parent=t1mirror,
                    art=art,
                )
                tier1_evo_dir = str(t1mirror)

            resume_qual: Optional[list] = None
            resume_pred: Optional[Dict[str, Any]] = None
            seed_json = ""
            merged_resume = False

            if next_iter > 1:
                resume_doc, seed_path = restore_checkpoint_into_workdir(
                    base_tiered=base_tiered,
                    job_id=job_id,
                    iteration_completed=next_iter - 1,
                    work_dir=work_dir,
                )
                resume_qual = resume_doc.get("qualifying_keys_for_next") or []
                resume_pred = resume_doc.get("pred_snapshot")
                if not isinstance(resume_pred, dict):
                    resume_pred = {}
                seed_json = str(seed_path)

            partial_hit = latest_partial_checkpoint(
                base_tiered=base_tiered,
                job_id=job_id,
                outer_iteration=next_iter,
            )
            if partial_hit is not None:
                partial_prefix, partial_doc = partial_hit
                _partial_resume, partial_seed = restore_partial_checkpoint_into_workdir(
                    base_tiered=base_tiered,
                    job_id=job_id,
                    s3_prefix=partial_prefix,
                    work_dir=work_dir,
                )
                seed_json = str(partial_seed)
                merged_resume = True
                if isinstance(partial_doc.get("pred_snapshot"), dict):
                    resume_pred = partial_doc["pred_snapshot"]
                for fp in partial_doc.get("failed_posts") or []:
                    if isinstance(fp, dict):
                        failed_posts_acc.append(fp)
                logger.info(
                    "[SGO_JOB] %s resumed mid–outer-iteration %s from partial %s",
                    job_id,
                    next_iter,
                    partial_doc.get("progress_reason"),
                )
            else:
                merged_resume = False

            mdl = model or job.get("model") or None

            merged: Dict[str, Any] = {
                "tier_mode": tier_mode,
                "contextual_json": str(contextual_path),
                "description_json": str(description_path),
                "mapping_json": str(mapping_path),
                "profiles_root": str(profiles_root),
                "precomputed_i0_json": str(i0_path),
                "work_dir": str(work_dir),
                "output": str(work_dir / "delta_method_predictions.json"),
                "tier1_evolution_work_dir": tier1_evo_dir,
                "start_iteration": next_iter,
                "num_iterations": next_iter,
                "sgo_user_pred_seed_json": seed_json,
                "sgo_user_pred_seed_json_out": str(work_dir / ".sgo_user_pred_seed_out.json"),
            }
            if next_iter > 1:
                merged["sgo_resume_qualifying_keys"] = resume_qual or []
                merged["sgo_resume_pred_snapshot"] = resume_pred or {}
            if partial_hit is not None or merged_resume:
                merged["resume"] = True
            if tier_int == 2 and next_iter > 1:
                merged["sgo_skip_tier1_evolution_requirement"] = True
            if mdl:
                for k in ("gen_model", "topic_model", "delta_model"):
                    merged.setdefault(k, mdl)

            ns = get_default_namespace(merged)
            ensure_required_paths(ns)
            setattr(ns, "sgo_failed_posts", failed_posts_acc)

            state_path = work_dir / "evolution" / "evolution_state.json"
            memory_path = work_dir / "memory" / "batch_analyses.json"
            trace_path = work_dir / "post_traces.jsonl"
            iteration_stats_path = work_dir / "iteration_stats.jsonl"
            out_path = work_dir / "delta_method_predictions.json"

            async def _on_progress(**payload: Any) -> None:
                fp = list(payload.get("failed_posts") or [])
                partial_doc = build_partial_resume_payload(
                    outer_iteration=int(payload["outer_iteration"]),
                    num_iterations_planned=num_it,
                    tier_mode=tier_mode,
                    progress_reason=str(payload.get("progress_reason") or ""),
                    processed_post_ids=list(payload.get("processed_post_ids") or []),
                    pred_snapshot_jsonable=dict(payload.get("user_pred_jsonable") or {}),
                    failed_posts=fp,
                )
                upload_partial_iteration_checkpoint(
                    base_tiered=base_tiered,
                    job_id=job_id,
                    outer_iteration=int(payload["outer_iteration"]),
                    progress_reason=str(payload.get("progress_reason") or ""),
                    partial_doc=partial_doc,
                    user_pred_jsonable=dict(payload["user_pred_jsonable"]),
                    work_dir=work_dir,
                    state_path=Path(payload["state_path"]),
                    memory_path=Path(payload["memory_path"]),
                    trace_path=Path(payload["trace_path"]),
                    iteration_stats_path=Path(payload["iteration_stats_path"]),
                    out_path=Path(payload["out_path"]),
                )
                job_now = get_linkedin_room_pipeline_job(job_id, enterprise_name=enterprise_name) or {}
                jrx = job_now.get("result") if isinstance(job_now.get("result"), dict) else {}
                update_linkedin_room_pipeline_job(
                    job_id,
                    enterprise_name=enterprise_name,
                    result={
                        **jrx,
                        "heartbeat_at": datetime.now(timezone.utc).isoformat(),
                        "sgo_last_progress_at": datetime.now(timezone.utc).isoformat(),
                        "sgo_last_progress_reason": str(payload.get("progress_reason") or ""),
                        "failed_posts": fp,
                        "failed_posts_count": len(fp),
                        "outer_iteration_running": int(payload["outer_iteration"]),
                    },
                    error=None,
                )

            async def _on_complete(**payload: Any) -> None:
                fp = list(payload.get("failed_posts") or [])
                resume_doc = build_resume_payload(
                    iteration_completed=int(payload["iteration_completed"]),
                    num_iterations_planned=num_it,
                    tier_mode=tier_mode,
                    qualifying_keys_for_next=list(payload["qualifying_keys_for_next"]),
                    pred_snapshot_jsonable=dict(payload["pred_snapshot_jsonable"]),
                )
                upload_iteration_checkpoint(
                    base_tiered=base_tiered,
                    job_id=job_id,
                    iteration_completed=int(payload["iteration_completed"]),
                    resume_doc=resume_doc,
                    user_pred_jsonable=dict(payload["user_pred_jsonable"]),
                    work_dir=work_dir,
                    state_path=Path(payload["state_path"]),
                    memory_path=Path(payload["memory_path"]),
                    trace_path=Path(payload["trace_path"]),
                    iteration_stats_path=Path(payload["iteration_stats_path"]),
                    out_path=Path(payload["out_path"]),
                )
                done_iter = int(payload["iteration_completed"])
                new_next = done_iter + 1
                for attempt in range(4):
                    try:
                        job_now = (
                            get_linkedin_room_pipeline_job(job_id, enterprise_name=enterprise_name)
                            or {}
                        )
                        jrx = (
                            job_now.get("result") if isinstance(job_now.get("result"), dict) else {}
                        )
                        posts_i = int(jrx.get("num_posts") or 0)
                        total_items_cb, completed_items_cb = _sgo_job_progress_counts(
                            posts_i, num_it, done_iter
                        )
                        update_linkedin_room_pipeline_job(
                            job_id,
                            enterprise_name=enterprise_name,
                            completed_items=completed_items_cb,
                            total_items=total_items_cb,
                            result={
                                **jrx,
                                "outer_iteration_next": new_next,
                                "num_iterations": num_it,
                                "tier_mode": tier_mode,
                                "tier_int": tier_int,
                                "heartbeat_at": datetime.now(timezone.utc).isoformat(),
                                "last_checkpoint_iteration": done_iter,
                                "failed_posts": fp,
                                "failed_posts_count": len(fp),
                            },
                            error=None,
                        )
                        break
                    except (psycopg2.OperationalError, psycopg2.InterfaceError) as db_err:
                        if attempt >= 3:
                            logger.error(
                                "[SGO_OBS] event=db_retry_exhausted_after_checkpoint job_id=%s error=%s",
                                job_id,
                                db_err,
                            )
                            raise LinkedInSGOPipelineError(
                                f"DB update failed after S3 checkpoint: {db_err}"
                            ) from db_err
                        logger.warning(
                            "[SGO_OBS] event=db_transient_after_checkpoint job_id=%s attempt=%s/4 error=%s",
                            job_id,
                            attempt + 1,
                            db_err,
                        )
                        await asyncio.sleep(0.12 * (2**attempt))
                if (
                    should_self_trigger_next_chunk(workflow_orchestrated=workflow_orchestrated)
                    and new_next <= num_it + 1
                ):
                    await _trigger_sgo_process_endpoint(
                        base_url, audience_room_id, job_id, enterprise_name, model
                    )

            setattr(ns, "sgo_on_outer_iteration_progress", _on_progress)
            setattr(ns, "sgo_on_outer_iteration_complete", _on_complete)

            try:
                await run_linkedin_sgo_pipeline_async(ns)
            finally:
                fp_final = list(getattr(ns, "sgo_failed_posts", None) or [])
                if fp_final:
                    job_now = (
                        get_linkedin_room_pipeline_job(job_id, enterprise_name=enterprise_name) or {}
                    )
                    jrx = job_now.get("result") if isinstance(job_now.get("result"), dict) else {}
                    update_linkedin_room_pipeline_job(
                        job_id,
                        enterprise_name=enterprise_name,
                        result={
                            **jrx,
                            "failed_posts": fp_final,
                            "failed_posts_count": len(fp_final),
                        },
                    )

    except LinkedInSGOPipelineError as e:
        _mark_sgo_job_failed(
            job_id=job_id,
            enterprise_name=enterprise_name,
            jr=jr,
            exc=e,
            failed_posts=list(locals().get("failed_posts_acc", []) or jr.get("failed_posts") or []),
        )
    except SystemExit as e:
        code = e.code if e.code is not None else 1
        if code not in (0, None):
            _mark_sgo_job_failed(
                job_id=job_id,
                enterprise_name=enterprise_name,
                jr=jr,
                exc=LinkedInSGOPipelineError(f"SGO pipeline exited with status {code}"),
                failed_posts=list(locals().get("failed_posts_acc", []) or jr.get("failed_posts") or []),
            )
    except Exception as e:
        _mark_sgo_job_failed(
            job_id=job_id,
            enterprise_name=enterprise_name,
            jr=jr,
            exc=e,
            failed_posts=list(locals().get("failed_posts_acc", []) or jr.get("failed_posts") or []),
        )
