"""Orchestrate DB job creation + ECS Fargate launch for LinkedIn SGO."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.services.linkedin_room_pipeline_async.linkedin_room_pipeline_job_repository import (
    get_linkedin_room_pipeline_job,
    update_linkedin_room_pipeline_job,
)
from app.services.user_profile_summarization_service.utils import get_base_url

from .ecs_client import SgoFargateLaunchError, build_webhook_url, launch_sgo_fargate_task
from .fargate_both_tier_jobs import build_fargate_both_tier_trigger

logger = logging.getLogger(__name__)

_TIER_BOTH_ALIASES = frozenset({"both", "all", "tier1_tier2", "tier1+tier2"})


def build_fargate_trigger_result(
    *,
    audience_room_id: str,
    enterprise_name: Optional[str] = None,
    model: Optional[str] = None,
    tier_mode: str = "both",
    num_iterations: int = 5,
) -> Dict[str, Any]:
    """Create tier1 + tier2 DB jobs before Fargate starts (poll-only friendly)."""
    return build_fargate_both_tier_trigger(
        audience_room_id=audience_room_id,
        enterprise_name=enterprise_name,
        model=model,
        num_iterations=num_iterations,
    )


def start_linkedin_sgo_on_fargate(
    *,
    trigger_result: Dict[str, Any],
    audience_room_id: str,
    enterprise_name: Optional[str] = None,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    workflow_resume_url: Optional[str] = None,
    tier_mode: Optional[str] = None,
    num_iterations: Optional[int] = None,
    notify_webhook: bool = False,
) -> Dict[str, Any]:
    """
    Launch Fargate after jobs exist (single-tier) or tier1+tier2 jobs were pre-created.

    Default ``notify_webhook=False``: poll ``tier_results[].poll`` or ``poll_pipeline``.
    Set ``notify_webhook=True`` to POST completion to ``/api/v1/sgo/fargate/webhook``.
    """
    job_id = str(trigger_result["job_id"])
    origin = (base_url or get_base_url()).rstrip("/")
    webhook_url = build_webhook_url(origin) if notify_webhook else ""

    tm = (tier_mode or trigger_result.get("tier_mode") or "both").strip().lower()
    n_it = num_iterations if num_iterations is not None else trigger_result.get("num_iterations")

    jr_extra: Dict[str, Any] = {
        "compute_backend": "fargate",
        "fargate_tier_mode": tm,
        "fargate_notify_webhook": bool(notify_webhook),
    }
    if notify_webhook:
        jr_extra["fargate_webhook_url"] = webhook_url
    if n_it is not None:
        jr_extra["fargate_num_iterations"] = int(n_it)
    if workflow_resume_url:
        jr_extra["workflow_resume_url"] = workflow_resume_url.strip()

    pipeline_mode = str(trigger_result.get("pipeline_mode") or "")
    tier1_job_id = str(trigger_result.get("tier1_job_id") or "") or None
    tier2_job_id = str(trigger_result.get("tier2_job_id") or "") or None
    pipeline_run_id = str(trigger_result.get("pipeline_run_id") or "") or None

    if pipeline_mode != "tier1_then_tier2":
        job = get_linkedin_room_pipeline_job(job_id, enterprise_name=enterprise_name) or {}
        existing = job.get("result") if isinstance(job.get("result"), dict) else {}
        update_linkedin_room_pipeline_job(
            job_id,
            enterprise_name=enterprise_name,
            status="PROCESSING",
            result={**existing, **jr_extra},
        )
    else:
        existing = {}
        for tid in (tier1_job_id, tier2_job_id):
            if not tid:
                continue
            job = get_linkedin_room_pipeline_job(tid, enterprise_name=enterprise_name) or {}
            jrx = job.get("result") if isinstance(job.get("result"), dict) else {}
            update_linkedin_room_pipeline_job(
                tid,
                enterprise_name=enterprise_name,
                status="PROCESSING",
                result={**jrx, **jr_extra},
            )

    try:
        task_response = launch_sgo_fargate_task(
            job_id=job_id,
            audience_room_id=audience_room_id,
            webhook_url=webhook_url or None,
            enterprise_name=enterprise_name,
            model=model,
            tier_mode=tm,
            num_iterations=int(n_it) if n_it is not None else 5,
            notify_webhook=notify_webhook,
            tier1_job_id=tier1_job_id,
            tier2_job_id=tier2_job_id,
            pipeline_run_id=pipeline_run_id,
        )
    except SgoFargateLaunchError as e:
        if pipeline_mode == "tier1_then_tier2":
            for tid in (tier1_job_id, tier2_job_id):
                if not tid:
                    continue
                job = get_linkedin_room_pipeline_job(tid, enterprise_name=enterprise_name) or {}
                jrx = job.get("result") if isinstance(job.get("result"), dict) else {}
                update_linkedin_room_pipeline_job(
                    tid,
                    enterprise_name=enterprise_name,
                    status="FAILED",
                    error=str(e),
                    result={**jrx, **jr_extra, "fargate_launch_error": str(e)},
                )
        else:
            update_linkedin_room_pipeline_job(
                job_id,
                enterprise_name=enterprise_name,
                status="FAILED",
                error=str(e),
                result={**existing, **jr_extra, "fargate_launch_error": str(e)},
            )
        raise

    tasks = task_response.get("tasks") or []
    task_arn = tasks[0].get("taskArn") if tasks else None
    if pipeline_mode != "tier1_then_tier2":
        update_linkedin_room_pipeline_job(
            job_id,
            enterprise_name=enterprise_name,
            result={**existing, **jr_extra, "fargate_task_arn": task_arn},
        )
    else:
        for tid in (tier1_job_id, tier2_job_id):
            if not tid:
                continue
            job = get_linkedin_room_pipeline_job(tid, enterprise_name=enterprise_name) or {}
            jrx = job.get("result") if isinstance(job.get("result"), dict) else {}
            update_linkedin_room_pipeline_job(
                tid,
                enterprise_name=enterprise_name,
                result={**jrx, **jr_extra, "fargate_task_arn": task_arn},
            )

    msg = str(trigger_result.get("message") or "")
    if not msg:
        if pipeline_mode == "tier1_then_tier2":
            msg = (
                "Fargate tier1→tier2 queued. Poll poll_pipeline or each tier_results[].poll."
            )
        else:
            msg = (
                f"SGO job {job_id} queued on AWS Fargate. Poll {trigger_result.get('poll')}."
            )

    out: Dict[str, Any] = {
        **trigger_result,
        "compute_backend": "fargate",
        "fargate_task_arn": task_arn,
        "notify_webhook": bool(notify_webhook),
        "message": msg,
    }
    if notify_webhook:
        out["webhook_url"] = webhook_url
    return out
