"""Pre-create DB jobs for tier1→tier2 Fargate runs (poll-only friendly)."""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from app.services.linkedin_room_pipeline_async.linkedin_room_pipeline_job_repository import (
    get_linkedin_room_pipeline_job,
    update_linkedin_room_pipeline_job,
)
from app.services.linkedin_room_pipeline_async.linkedin_room_pipeline_async_handler import (
    LinkedInRoomPipelineAsyncHandler,
)

_handler = LinkedInRoomPipelineAsyncHandler()


def build_fargate_both_tier_trigger(
    *,
    audience_room_id: str,
    enterprise_name: Optional[str] = None,
    model: Optional[str] = None,
    num_iterations: int = 5,
) -> Dict[str, Any]:
    """
    Create real tier1 + tier2 SGO jobs up front so clients can poll immediately.

    Returns ``tier_results`` with ``job_id`` and ``poll`` per tier.
    """
    pipeline_run_id = str(uuid.uuid4())
    tier_results: List[Dict[str, Any]] = []

    for tm in ("tier1", "tier2"):
        trigger = _handler.trigger_async_linkedin_sgo_pipeline(
            audience_room_id=audience_room_id,
            enterprise_name=enterprise_name,
            model=model,
            tier_mode=tm,
            num_iterations=num_iterations,
        )
        job_id = str(trigger["job_id"])
        job = get_linkedin_room_pipeline_job(job_id, enterprise_name=enterprise_name) or {}
        jr = job.get("result") if isinstance(job.get("result"), dict) else {}
        update_linkedin_room_pipeline_job(
            job_id,
            enterprise_name=enterprise_name,
            result={
                **jr,
                "fargate_pipeline_run_id": pipeline_run_id,
                "fargate_pipeline_tier": tm,
                "compute_backend": "fargate",
            },
        )
        tier_results.append(
            {
                "tier_mode": tm,
                "job_id": job_id,
                "status": str(trigger.get("status") or "PENDING"),
                "poll": trigger.get("poll"),
            }
        )

    tier1_job_id = tier_results[0]["job_id"]
    return {
        "job_id": tier1_job_id,
        "pipeline_run_id": pipeline_run_id,
        "pipeline_mode": "tier1_then_tier2",
        "tier_mode": "both",
        "status": "PROCESSING",
        "audience_room_id": audience_room_id,
        "num_iterations": num_iterations,
        "tier_results": tier_results,
        "tier1_job_id": tier1_job_id,
        "tier2_job_id": tier_results[1]["job_id"],
        "poll_pipeline": (
            f"/api/v1/audience-rooms/{audience_room_id}/linkedin-sgo-pipeline/fargate/pipeline-status"
            f"?tier1JobId={tier1_job_id}&tier2JobId={tier_results[1]['job_id']}"
        ),
        "message": (
            "Fargate tier1→tier2 queued. Poll poll_pipeline or each tier_results[].poll "
            "until pipeline_status is COMPLETED or FAILED."
        ),
    }
