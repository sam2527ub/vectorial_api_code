"""Aggregate poll status for tier1→tier2 Fargate SGO pipelines."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.linkedin_room_pipeline_async.linkedin_room_pipeline_job_repository import (
    JOB_TYPE_LINKEDIN_SGO_PIPELINE,
    get_linkedin_room_pipeline_job,
)


def _tier_entry_from_job(job: Dict[str, Any], *, tier_mode: str) -> Dict[str, Any]:
    jr = job.get("result") if isinstance(job.get("result"), dict) else {}
    room_id = str(job.get("audience_room_id") or "")
    job_id = str(job.get("job_id") or "")
    poll = (
        f"/api/v1/audience-rooms/{room_id}/linkedin-sgo-pipeline/async/status/{job_id}"
        if room_id and job_id
        else ""
    )
    return {
        "tier_mode": tier_mode,
        "job_id": job_id,
        "status": str(job.get("status") or "UNKNOWN"),
        "error": job.get("error"),
        "poll": poll,
        "completed_items": job.get("completed_items"),
        "total_items": job.get("total_items"),
        "outer_iteration_next": jr.get("outer_iteration_next"),
        "num_iterations": jr.get("num_iterations"),
        "failed_posts_count": jr.get("failed_posts_count"),
        "sgo_early_stop": jr.get("sgo_early_stop"),
        "sgo_early_stop_reason": jr.get("sgo_early_stop_reason"),
        "last_checkpoint_iteration": jr.get("last_checkpoint_iteration"),
        "sgo_resume_blocked": jr.get("sgo_resume_blocked"),
        "compute_backend": jr.get("compute_backend"),
    }


def aggregate_fargate_pipeline_status(
    *,
    audience_room_id: str,
    tier1_job_id: str,
    tier2_job_id: Optional[str] = None,
    enterprise_name: Optional[str] = None,
    pipeline_run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Poll-friendly rollup for ``tierMode=both``.

    Pipeline is ``COMPLETED`` only when every listed tier job is ``COMPLETED``.
    """
    tier_specs = [("tier1", tier1_job_id)]
    if tier2_job_id:
        tier_specs.append(("tier2", tier2_job_id))

    tier_results: List[Dict[str, Any]] = []
    terminal_failure = False
    failed_tier: Optional[str] = None

    for tm, jid in tier_specs:
        job = get_linkedin_room_pipeline_job(jid, enterprise_name=enterprise_name) or {}
        if job.get("job_type") != JOB_TYPE_LINKEDIN_SGO_PIPELINE:
            tier_results.append(
                {
                    "tier_mode": tm,
                    "job_id": jid,
                    "status": "FAILED",
                    "error": f"Job {jid} not found or wrong type",
                    "poll": "",
                }
            )
            terminal_failure = True
            failed_tier = failed_tier or tm
            continue
        if str(job.get("audience_room_id") or "") != audience_room_id:
            tier_results.append(
                {
                    "tier_mode": tm,
                    "job_id": jid,
                    "status": "FAILED",
                    "error": "audience_room_id mismatch",
                    "poll": "",
                }
            )
            terminal_failure = True
            failed_tier = failed_tier or tm
            continue

        entry = _tier_entry_from_job(job, tier_mode=tm)
        tier_results.append(entry)
        st = str(entry.get("status") or "").upper()
        if st == "FAILED":
            terminal_failure = True
            failed_tier = failed_tier or tm

    if len(tier_results) >= 2:
        t1_st = str(tier_results[0].get("status") or "").upper()
        t2 = dict(tier_results[1])
        t2_st = str(t2.get("status") or "").upper()
        if t1_st != "COMPLETED" and t2_st in ("PROCESSING", "PENDING"):
            t2["status"] = "PENDING"
            t2["waiting_for_tier1"] = True
            if t1_st == "FAILED":
                t2["message"] = "Waiting for tier1 to complete before tier2 starts."
            else:
                t2["message"] = "Tier2 starts automatically after tier1 completes."
            tier_results[1] = t2

    statuses = {str(e.get("status") or "").upper() for e in tier_results}
    if terminal_failure:
        pipeline_status = "FAILED"
    elif statuses <= {"COMPLETED"} and statuses == {"COMPLETED"}:
        pipeline_status = "COMPLETED"
    elif statuses & {"PROCESSING", "PENDING"}:
        pipeline_status = "PROCESSING"
    else:
        pipeline_status = "PROCESSING"

    out: Dict[str, Any] = {
        "pipeline_mode": "tier1_then_tier2" if tier2_job_id else "single_tier",
        "pipeline_status": pipeline_status,
        "audience_room_id": audience_room_id,
        "tier_results": tier_results,
        "poll_mode": "status_endpoint",
        "message": (
            "Poll each tier_results[].poll until pipeline_status is COMPLETED or FAILED."
        ),
    }
    if pipeline_run_id:
        out["pipeline_run_id"] = pipeline_run_id
    if failed_tier:
        out["failed_tier"] = failed_tier
    return out
