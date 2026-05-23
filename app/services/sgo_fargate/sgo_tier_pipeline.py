"""Run LinkedIn SGO tier1 → tier2 in order (production Fargate / full pipeline)."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.services.linkedin_room_pipeline_async.linkedin_room_pipeline_async_handler import (
    LinkedInRoomPipelineAsyncHandler,
)

from .sgo_fargate_worker_loop import run_sgo_training_loop

logger = logging.getLogger(__name__)

_handler = LinkedInRoomPipelineAsyncHandler()
_DEFAULT_TIER_ORDER: List[str] = ["tier1", "tier2"]


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
) -> Dict[str, Any]:
    """
    Create one DB job per tier and run all outer-iteration chunks in-process.

    When ``tier_job_ids`` is provided (``{"tier1": "...", "tier2": "..."}``), reuse
    pre-created jobs from the API instead of creating new ones (poll-only Fargate start).
    """
    modes = normalize_tier_modes(tier_mode)
    tier_results: List[Dict[str, Any]] = []
    pre = dict(tier_job_ids or {})

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
        outcome = await run_sgo_training_loop(
            job_id=job_id,
            audience_room_id=audience_room_id,
            enterprise_name=enterprise_name,
            model=model,
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
