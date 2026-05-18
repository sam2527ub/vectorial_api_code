"""Helpers when Vercel Workflow (not FastAPI) chains chunked async jobs."""

from __future__ import annotations

from typing import Any, Dict, Optional

from .linkedin_room_pipeline_job_repository import get_linkedin_room_pipeline_job


def should_self_trigger_next_chunk(*, workflow_orchestrated: bool) -> bool:
    """When True, workers may HTTP-trigger the next /async/process chunk."""
    return not workflow_orchestrated


def build_process_chunk_response(
    *,
    job_id: str,
    enterprise_name: Optional[str],
    workflow_orchestrated: bool,
) -> Dict[str, Any]:
    job = get_linkedin_room_pipeline_job(job_id, enterprise_name=enterprise_name) or {}
    status = str(job.get("status") or "UNKNOWN")
    needs_continue = workflow_orchestrated and status == "PROCESSING"
    return {
        "job_id": job_id,
        "status": status,
        "needs_continue": needs_continue,
    }
