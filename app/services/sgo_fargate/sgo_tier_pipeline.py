"""Run LinkedIn SGO tier1 → tier2 in order (production Fargate / full pipeline)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.services.linkedin_room_pipeline_async.linkedin_room_pipeline_job_repository import (
    get_linkedin_room_pipeline_job,
    update_linkedin_room_pipeline_job,
)
from app.services.linkedin_room_pipeline_async.linkedin_room_pipeline_async_handler import (
    LinkedInRoomPipelineAsyncHandler,
)

from .sgo_fargate_worker_loop import run_sgo_training_loop

logger = logging.getLogger(__name__)

_handler = LinkedInRoomPipelineAsyncHandler()
_DEFAULT_TIER_ORDER: List[str] = ["tier1", "tier2"]


def _mark_tier_job_status(
    *,
    job_id: str,
    enterprise_name: Optional[str],
    status: str,
    result_extra: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
) -> None:
    job = get_linkedin_room_pipeline_job(job_id, enterprise_name=enterprise_name) or {}
    jr = job.get("result") if isinstance(job.get("result"), dict) else {}
    payload = {**jr, **(result_extra or {})}
    update_linkedin_room_pipeline_job(
        job_id,
        enterprise_name=enterprise_name,
        status=status,
        error=error,
        result=payload,
    )


def _mark_tier2_waiting_for_tier1(
    *,
    tier2_job_id: str,
    enterprise_name: Optional[str],
    tier1_error: Optional[str] = None,
) -> None:
    _mark_tier_job_status(
        job_id=tier2_job_id,
        enterprise_name=enterprise_name,
        status="PENDING",
        error=None,
        result_extra={
            "phase": "waiting_for_tier1",
            "waiting_for_tier1": True,
            "blocked_by_tier1_failure": bool(tier1_error),
            "tier1_error": tier1_error,
            "heartbeat_at": datetime.now(timezone.utc).isoformat(),
        },
    )


def normalize_tier_modes(tier_mode: Optional[str]) -> List[str]:
    """
    ``tier1`` | ``tier2`` | ``both`` (default for full pipeline).

    ``both`` runs tier1 to completion, then tier2 using tier1 ``evolution_state`` on S3.
    """
    raw = (tier_mode or "both").strip().lower()
    if raw in ("both", "all", "tier1_tier2", "tier1+tier2"):
        return list(_DEFAULT_TIER_ORDER)
    if raw in ("tier1", "tier2"):
        return [raw]
    return list(_DEFAULT_TIER_ORDER)


async def run_sgo_tier_pipeline(
    *,
    audience_room_id: str,
    enterprise_name: Optional[str] = None,
    model: Optional[str] = None,
    num_iterations: int = 5,
    tier_mode: Optional[str] = "both",
    tier_job_ids: Optional[Dict[str, str]] = None,
    force_resume: bool = False,
) -> Dict[str, Any]:
    """
    Create one DB job per tier and run all outer-iteration chunks in-process.

    When ``tier_job_ids`` is provided (``{"tier1": "...", "tier2": "..."}``), reuse
    pre-created jobs from the API instead of creating new ones (poll-only Fargate start).
    """
    modes = normalize_tier_modes(tier_mode)
    tier_results: List[Dict[str, Any]] = []
    pre = dict(tier_job_ids or {})
    tier2_job_id = str(pre.get("tier2") or "").strip()

    for tm in modes:
        logger.info(
            "[SGO_TIER_PIPELINE] starting tier=%s room=%s enterprise=%s",
            tm,
            audience_room_id,
            enterprise_name,
        )
        existing_job_id = str(pre.get(tm) or "").strip()
        if existing_job_id:
            job_id = existing_job_id
            poll = (
                f"/api/v1/audience-rooms/{audience_room_id}/"
                f"linkedin-sgo-pipeline/async/status/{job_id}"
            )
            trigger = {"job_id": job_id, "poll": poll}
        else:
            trigger = _handler.trigger_async_linkedin_sgo_pipeline(
                audience_room_id=audience_room_id,
                enterprise_name=enterprise_name,
                model=model,
                tier_mode=tm,
                num_iterations=num_iterations,
            )
            job_id = str(trigger["job_id"])
            if tm == "tier2":
                tier2_job_id = job_id

        if tm == "tier2":
            _mark_tier_job_status(
                job_id=job_id,
                enterprise_name=enterprise_name,
                status="PROCESSING",
                result_extra={
                    "phase": "running_outer_iteration",
                    "waiting_for_tier1": False,
                    "outer_iteration_running": 1,
                    "heartbeat_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            logger.info(
                "[SGO_TIER_PIPELINE] tier1 complete — auto-starting tier2 job=%s",
                job_id,
            )

        outcome = await run_sgo_training_loop(
            job_id=job_id,
            audience_room_id=audience_room_id,
            enterprise_name=enterprise_name,
            model=model,
            force_resume=force_resume,
        )
        status = str(outcome.get("status") or "FAILED").upper()
        tier_results.append(
            {
                "tier_mode": tm,
                "job_id": job_id,
                "status": status,
                "error": outcome.get("error"),
                "poll": trigger.get("poll"),
            }
        )
        if status != "COMPLETED":
            if tm == "tier1" and tier2_job_id:
                _mark_tier2_waiting_for_tier1(
                    tier2_job_id=tier2_job_id,
                    enterprise_name=enterprise_name,
                    tier1_error=str(outcome.get("error") or ""),
                )
            return {
                "status": "FAILED",
                "failed_tier": tm,
                "tier_results": tier_results,
                "error": outcome.get("error") or f"SGO {tm} did not complete",
            }

    return {
        "status": "COMPLETED",
        "tier_results": tier_results,
        "message": f"SGO completed for tier(s): {', '.join(modes)}",
    }
