"""Resume failed/stale Fargate SGO jobs from existing DB job ids + S3 checkpoints."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from app import database
from app.linkedin_tiered_s3 import get_linkedin_tiered_s3_artifact_names
from app.services.linkedin_room_pipeline_async.linkedin_room_pipeline_job_repository import (
    JOB_TYPE_LINKEDIN_SGO_PIPELINE,
    get_linkedin_room_pipeline_job,
    update_linkedin_room_pipeline_job,
)
from app.services.linkedin_sgo_pipeline_service_async.sgo_job_resume import (
    assess_sgo_job_resume,
    prepare_job_for_resume,
)
from app.utils.s3_utils import get_audience_type_from_source, get_s3_key_for_audience

from .ecs_client import SgoFargateLaunchError, launch_sgo_fargate_task
from .fargate_launcher import start_linkedin_sgo_on_fargate

logger = logging.getLogger(__name__)


class SgoFargateResumeError(ValueError):
    """Resume preflight failed (no checkpoint, wrong status, etc.)."""


def _base_tiered_for_room(
    *,
    audience_room_id: str,
    enterprise_name: Optional[str],
) -> str:
    room = database.find_audience_room_by_id(
        audience_room_id, include_profiles=False, enterprise_name=enterprise_name
    )
    if not room:
        raise SgoFargateResumeError(f"Audience room not found: {audience_room_id}")
    if get_audience_type_from_source(room.source) != "linkedin-audience":
        raise SgoFargateResumeError("Room is not a LinkedIn audience")
    art = get_linkedin_tiered_s3_artifact_names()
    return get_s3_key_for_audience(
        audience_room_id, art.tiered_folder, enterprise_name, room.source
    ).rstrip("/")


def _load_tier_job(
    *,
    job_id: str,
    audience_room_id: str,
    enterprise_name: Optional[str],
    tier_mode: str,
) -> Dict[str, Any]:
    job = get_linkedin_room_pipeline_job(job_id, enterprise_name=enterprise_name) or {}
    if not job:
        raise SgoFargateResumeError(f"Job not found: {job_id}")
    if job.get("job_type") != JOB_TYPE_LINKEDIN_SGO_PIPELINE:
        raise SgoFargateResumeError(f"Job {job_id} is not linkedin_sgo_pipeline")
    if str(job.get("audience_room_id") or "") != audience_room_id:
        raise SgoFargateResumeError(f"Job {job_id} audience_room_id mismatch")
    jr = job.get("result") if isinstance(job.get("result"), dict) else {}
    tm = str(jr.get("tier_mode") or jr.get("fargate_pipeline_tier") or tier_mode).strip().lower()
    if tm != tier_mode:
        raise SgoFargateResumeError(f"Job {job_id} tier_mode={tm!r}, expected {tier_mode!r}")
    return job


def assess_fargate_pipeline_resume(
    *,
    audience_room_id: str,
    tier1_job_id: str,
    tier2_job_id: Optional[str] = None,
    enterprise_name: Optional[str] = None,
    force: bool = True,
) -> Dict[str, Any]:
    """Inspect whether tier jobs can resume from S3 without launching Fargate."""
    base_tiered = _base_tiered_for_room(
        audience_room_id=audience_room_id,
        enterprise_name=enterprise_name,
    )
    tier_specs: List[Tuple[str, str]] = [("tier1", tier1_job_id)]
    if tier2_job_id:
        tier_specs.append(("tier2", tier2_job_id))

    tier_results: List[Dict[str, Any]] = []
    can_resume_any = False
    for tm, jid in tier_specs:
        job = _load_tier_job(
            job_id=jid,
            audience_room_id=audience_room_id,
            enterprise_name=enterprise_name,
            tier_mode=tm,
        )
        status = str(job.get("status") or "").upper()
        if status == "COMPLETED":
            tier_results.append(
                {
                    "tier_mode": tm,
                    "job_id": jid,
                    "status": status,
                    "can_resume": False,
                    "reason": "already_completed",
                }
            )
            continue
        ok, reason, ckpt = assess_sgo_job_resume(
            job, base_tiered=base_tiered, force=force
        )
        tier_results.append(
            {
                "tier_mode": tm,
                "job_id": jid,
                "status": status,
                "can_resume": ok,
                "reason": reason,
                "checkpoint": ckpt,
            }
        )
        can_resume_any = can_resume_any or ok

    resume_tier: Optional[str] = None
    if tier_results:
        t1 = tier_results[0]
        if t1.get("can_resume"):
            resume_tier = "tier1"
        elif len(tier_results) > 1 and tier_results[1].get("can_resume"):
            if t1.get("status") == "COMPLETED":
                resume_tier = "tier2"

    return {
        "audience_room_id": audience_room_id,
        "pipeline_mode": "tier1_then_tier2" if tier2_job_id else "single_tier",
        "can_resume": can_resume_any,
        "resume_tier": resume_tier,
        "tier_results": tier_results,
    }


def _reset_jobs_for_resume(
    *,
    audience_room_id: str,
    tier1_job_id: str,
    tier2_job_id: Optional[str],
    enterprise_name: Optional[str],
    base_tiered: str,
    force: bool,
    jr_extra: Dict[str, Any],
) -> Dict[str, Any]:
    assessment = assess_fargate_pipeline_resume(
        audience_room_id=audience_room_id,
        tier1_job_id=tier1_job_id,
        tier2_job_id=tier2_job_id,
        enterprise_name=enterprise_name,
        force=force,
    )
    if not assessment.get("can_resume"):
        reasons = [
            f"{tr['tier_mode']}:{tr.get('reason')}"
            for tr in assessment.get("tier_results") or []
            if not tr.get("can_resume") and tr.get("status") != "COMPLETED"
        ]
        raise SgoFargateResumeError(
            "No tier job can resume from S3 checkpoints. "
            + (", ".join(reasons) if reasons else "no eligible checkpoint")
        )

    pipeline_run_id: Optional[str] = None
    for tm, jid in (("tier1", tier1_job_id), ("tier2", tier2_job_id or "")):
        if not jid:
            continue
        job = _load_tier_job(
            job_id=jid,
            audience_room_id=audience_room_id,
            enterprise_name=enterprise_name,
            tier_mode=tm,
        )
        status = str(job.get("status") or "").upper()
        if status == "COMPLETED":
            continue
        _ok, _reason, ckpt = assess_sgo_job_resume(
            job, base_tiered=base_tiered, force=force
        )
        if not _ok:
            continue
        jr = job.get("result") if isinstance(job.get("result"), dict) else {}
        pipeline_run_id = pipeline_run_id or str(jr.get("fargate_pipeline_run_id") or "") or None
        prepared = prepare_job_for_resume(
            job,
            checkpoint_state=ckpt,
            extra_result={**jr_extra, "fargate_pipeline_tier": tm},
        )
        update_linkedin_room_pipeline_job(
            jid,
            enterprise_name=enterprise_name,
            status="PROCESSING",
            error=None,
            result=prepared,
        )
        logger.info(
            "[SGO_FARGATE_RESUME] reset job %s tier=%s outer_iteration_next=%s",
            jid,
            tm,
            prepared.get("outer_iteration_next"),
        )

    poll_pipeline = (
        f"/api/v1/audience-rooms/{audience_room_id}/linkedin-sgo-pipeline/fargate/pipeline-status"
        f"?tier1JobId={tier1_job_id}"
        + (f"&tier2JobId={tier2_job_id}" if tier2_job_id else "")
    )
    tier_results: List[Dict[str, Any]] = []
    for tm, jid in (("tier1", tier1_job_id), ("tier2", tier2_job_id or "")):
        if not jid:
            continue
        job = get_linkedin_room_pipeline_job(jid, enterprise_name=enterprise_name) or {}
        tier_results.append(
            {
                "tier_mode": tm,
                "job_id": jid,
                "status": str(job.get("status") or "PROCESSING"),
                "poll": (
                    f"/api/v1/audience-rooms/{audience_room_id}/"
                    f"linkedin-sgo-pipeline/async/status/{jid}"
                ),
            }
        )

    return {
        "job_id": tier1_job_id,
        "pipeline_mode": "tier1_then_tier2" if tier2_job_id else "single_tier",
        "pipeline_run_id": pipeline_run_id,
        "tier_mode": "both" if tier2_job_id else "tier1",
        "status": "PROCESSING",
        "audience_room_id": audience_room_id,
        "tier_results": tier_results,
        "tier1_job_id": tier1_job_id,
        "tier2_job_id": tier2_job_id,
        "poll_pipeline": poll_pipeline,
        "resume_tier": assessment.get("resume_tier"),
        "message": (
            "Fargate resume queued from S3 checkpoint. Poll poll_pipeline or tier_results[].poll."
        ),
    }


def resume_linkedin_sgo_on_fargate(
    *,
    audience_room_id: str,
    tier1_job_id: str,
    tier2_job_id: Optional[str] = None,
    enterprise_name: Optional[str] = None,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    num_iterations: Optional[int] = None,
    notify_webhook: bool = False,
    force: bool = True,
) -> Dict[str, Any]:
    """
    Re-launch Fargate for existing tier job ids, restoring from S3 checkpoints.

    Does not create new DB jobs. Requires partial or completed outer-iteration
    checkpoints under ``linkedin_sgo/checkpoints/{job_id}/``.
    """
    base_tiered = _base_tiered_for_room(
        audience_room_id=audience_room_id,
        enterprise_name=enterprise_name,
    )
    jr_extra: Dict[str, Any] = {
        "compute_backend": "fargate",
        "fargate_tier_mode": "both" if tier2_job_id else "tier1",
        "fargate_notify_webhook": bool(notify_webhook),
        "fargate_force_resume": True,
    }
    if num_iterations is not None:
        jr_extra["fargate_num_iterations"] = int(num_iterations)

    trigger_result = _reset_jobs_for_resume(
        audience_room_id=audience_room_id,
        tier1_job_id=tier1_job_id,
        tier2_job_id=tier2_job_id,
        enterprise_name=enterprise_name,
        base_tiered=base_tiered,
        force=force,
        jr_extra=jr_extra,
    )

    n_it = num_iterations
    if n_it is None:
        t1 = get_linkedin_room_pipeline_job(tier1_job_id, enterprise_name=enterprise_name) or {}
        t1r = t1.get("result") if isinstance(t1.get("result"), dict) else {}
        n_it = int(t1r.get("num_iterations") or t1r.get("fargate_num_iterations") or 5)

    return start_linkedin_sgo_on_fargate(
        trigger_result=trigger_result,
        audience_room_id=audience_room_id,
        enterprise_name=enterprise_name,
        model=model,
        base_url=base_url,
        tier_mode="both" if tier2_job_id else "tier1",
        num_iterations=int(n_it),
        notify_webhook=notify_webhook,
        force_resume=True,
    )
