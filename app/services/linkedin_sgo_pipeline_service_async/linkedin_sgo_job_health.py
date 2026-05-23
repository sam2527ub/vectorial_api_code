"""Stale PROCESSING detection for long-running LinkedIn SGO jobs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts or not str(ts).strip():
        return None
    raw = str(ts).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def job_heartbeat_at(job: Dict[str, Any]) -> Optional[datetime]:
    jr = job.get("result") if isinstance(job.get("result"), dict) else {}
    for key in ("heartbeat_at", "sgo_last_progress_at"):
        dt = _parse_iso(jr.get(key))
        if dt is not None:
            return dt
    return _parse_iso(job.get("updated_at"))


def is_sgo_job_stale(
    job: Dict[str, Any],
    *,
    stale_timeout_s: float,
    now: Optional[datetime] = None,
) -> bool:
    """True when a PROCESSING job has not heartbeated within ``stale_timeout_s``."""
    if str(job.get("status") or "") != "PROCESSING":
        return False
    hb = job_heartbeat_at(job)
    if hb is None:
        return False
    ref = now or datetime.now(timezone.utc)
    age_s = (ref - hb).total_seconds()
    return age_s > float(stale_timeout_s)


def reconcile_stale_sgo_job(
    job: Dict[str, Any],
    *,
    stale_timeout_s: float,
    now: Optional[datetime] = None,
) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """
    If the job is stale PROCESSING, return ``(True, fail_result_meta)`` for the caller to persist.

    Otherwise ``(False, None)``.
    """
    if not is_sgo_job_stale(job, stale_timeout_s=stale_timeout_s, now=now):
        return False, None
    hb = job_heartbeat_at(job)
    ref = now or datetime.now(timezone.utc)
    age_s = int((ref - hb).total_seconds()) if hb else -1
    jr = job.get("result") if isinstance(job.get("result"), dict) else {}
    fail = {
        **jr,
        "sgo_failure_class": "stale_processing",
        "sgo_resume_blocked": False,
        "sgo_failed_at": ref.isoformat(),
        "stale_processing_age_s": age_s,
        "stale_processing_timeout_s": float(stale_timeout_s),
    }
    return True, fail


def maybe_mark_stale_sgo_job_failed(
    job: Dict[str, Any],
    *,
    job_id: str,
    enterprise_name: Optional[str] = None,
    stale_timeout_s: float,
) -> Dict[str, Any]:
    """If ``job`` is stale PROCESSING, persist FAILED and return the refreshed job dict."""
    is_stale, fail_meta = reconcile_stale_sgo_job(job, stale_timeout_s=stale_timeout_s)
    if not is_stale or fail_meta is None:
        return job

    from app.services.linkedin_room_pipeline_async.linkedin_room_pipeline_job_repository import (
        get_linkedin_room_pipeline_job,
        update_linkedin_room_pipeline_job,
    )

    update_linkedin_room_pipeline_job(
        job_id,
        enterprise_name=enterprise_name,
        status="FAILED",
        error=(
            f"Job stale in PROCESSING (no heartbeat for "
            f"{fail_meta.get('stale_processing_age_s')}s)"
        ),
        result=fail_meta,
    )
    return get_linkedin_room_pipeline_job(job_id, enterprise_name=enterprise_name) or job
