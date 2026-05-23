"""Assess and prepare SGO job resume from S3 checkpoints (completed + partial)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from .linkedin_sgo_pipeline_async_checkpoint import (
    latest_completed_checkpoint_iteration,
    latest_partial_checkpoint,
)


def sgo_checkpoint_resume_state(
    *,
    base_tiered: str,
    job_id: str,
    num_iterations: int,
    outer_iteration_next: int = 1,
) -> Dict[str, Any]:
    """
    Summarize S3 checkpoint state for a job.

    ``has_resume_checkpoint`` is true when a completed outer-iteration checkpoint
    exists or a partial checkpoint exists for the effective next outer iteration.
    """
    num_it = max(1, int(num_iterations))
    next_iter = max(1, int(outer_iteration_next))
    s3_latest = latest_completed_checkpoint_iteration(
        base_tiered=base_tiered,
        job_id=job_id,
        scan_max=num_it,
    )
    effective_next = max(next_iter, s3_latest + 1) if s3_latest > 0 else next_iter
    partial_hit = latest_partial_checkpoint(
        base_tiered=base_tiered,
        job_id=job_id,
        outer_iteration=effective_next,
    )
    partial_info: Optional[Dict[str, Any]] = None
    if partial_hit is not None:
        _prefix, partial_doc = partial_hit
        partial_info = {
            "progress_reason": partial_doc.get("progress_reason"),
            "heartbeat_at": partial_doc.get("heartbeat_at"),
            "outer_iteration": partial_doc.get("outer_iteration"),
        }
    return {
        "completed_outer_iterations": s3_latest,
        "outer_iteration_next": effective_next,
        "partial_checkpoint": partial_info,
        "has_resume_checkpoint": s3_latest > 0 or partial_hit is not None,
    }


def assess_sgo_job_resume(
    job: Dict[str, Any],
    *,
    base_tiered: str,
    force: bool = False,
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Return ``(can_resume, reason, checkpoint_state)``.

    ``force=True`` allows resume even when ``sgo_resume_blocked`` was set on failure
    (e.g. fixed config bug + partial checkpoint on S3).
    """
    status = str(job.get("status") or "").upper()
    if status == "COMPLETED":
        return False, "already_completed", {}

    jr = job.get("result") if isinstance(job.get("result"), dict) else {}
    job_id = str(job.get("job_id") or "")
    if not job_id:
        return False, "missing_job_id", {}

    num_it = max(1, int(jr.get("num_iterations") or jr.get("fargate_num_iterations") or 5))
    next_iter = max(1, int(jr.get("outer_iteration_next") or 1))
    ckpt = sgo_checkpoint_resume_state(
        base_tiered=base_tiered,
        job_id=job_id,
        num_iterations=num_it,
        outer_iteration_next=next_iter,
    )

    if not ckpt.get("has_resume_checkpoint"):
        return False, "no_checkpoint", ckpt

    if jr.get("sgo_resume_blocked") is True and not force:
        return False, "resume_blocked", ckpt

    if status not in ("FAILED", "PROCESSING", "PENDING"):
        return False, f"invalid_status_{status.lower()}", ckpt

    return True, "ok", ckpt


def prepare_job_for_resume(
    job: Dict[str, Any],
    *,
    checkpoint_state: Dict[str, Any],
    extra_result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build result payload fields to persist when re-queueing a failed/stale job."""
    jr = job.get("result") if isinstance(job.get("result"), dict) else {}
    now = datetime.now(timezone.utc).isoformat()
    next_iter = max(1, int(checkpoint_state.get("outer_iteration_next") or jr.get("outer_iteration_next") or 1))
    out = {
        **jr,
        "outer_iteration_next": next_iter,
        "sgo_resume_blocked": False,
        "sgo_resumed_at": now,
        "sgo_force_resume": True,
        "heartbeat_at": now,
        "sgo_last_progress_at": now,
    }
    if extra_result:
        out.update(extra_result)
    return out
