"""Handle Fargate worker webhook callbacks (resume external workflow)."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

import httpx

from app.runtime_settings import get_runtime_settings
from app.services.linkedin_room_pipeline_async.linkedin_room_pipeline_job_repository import (
    JOB_TYPE_LINKEDIN_SGO_PIPELINE,
    get_linkedin_room_pipeline_job,
    update_linkedin_room_pipeline_job,
)
from app.services.sgo_fargate.webhook_delivery import post_sgo_webhook_with_retries

logger = logging.getLogger(__name__)


def _verify_webhook_secret(provided: Optional[str]) -> bool:
    expected = (os.getenv("SGO_FARGATE_WEBHOOK_SECRET") or "").strip()
    if not expected:
        return True
    return (provided or "").strip() == expected


async def _notify_workflow_resume(
    *,
    resume_url: str,
    job_id: str,
    status: str,
    audience_room_id: Optional[str],
    error: Optional[str],
    tier_results: Optional[List[Dict[str, Any]]] = None,
) -> bool:
    payload = {
        "job_id": job_id,
        "status": status,
        "audience_room_id": audience_room_id,
        "error": error,
        "source": "sgo_fargate_webhook",
        "tier_results": tier_results,
    }
    sgo_cfg = get_runtime_settings().linkedin_sgo_async
    result = await post_sgo_webhook_with_retries(
        webhook_url=resume_url,
        payload=payload,
        secret=None,
        max_attempts=int(sgo_cfg.webhook_max_attempts),
        initial_backoff_s=float(sgo_cfg.webhook_initial_backoff_s),
        timeout_s=30.0,
    )
    if result.get("ok"):
        logger.info(
            "[SGO_FARGATE_WEBHOOK] Notified workflow resume url for job %s status=%s",
            job_id,
            status,
        )
        return True
    logger.error(
        "[SGO_FARGATE_WEBHOOK] workflow resume POST failed for job %s: %s",
        job_id,
        result.get("error"),
    )
    return False


def _resolve_webhook_jobs(body: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Map webhook body to one or more DB jobs (tier pipeline may send tier_results only)."""
    tier_results = body.get("tier_results")
    if isinstance(tier_results, list) and tier_results:
        jobs: List[Dict[str, Any]] = []
        for tr in tier_results:
            if not isinstance(tr, dict):
                continue
            jid = str(tr.get("job_id") or "").strip()
            if not jid:
                continue
            jobs.append(
                {
                    "job_id": jid,
                    "status": str(tr.get("status") or body.get("status") or "").upper(),
                    "error": tr.get("error") or body.get("error"),
                    "tier_mode": tr.get("tier_mode"),
                }
            )
        if jobs:
            return jobs

    job_id = str(body.get("job_id") or "").strip()
    if not job_id:
        return []
    return [
        {
            "job_id": job_id,
            "status": str(body.get("status") or "").upper(),
            "error": body.get("error"),
            "tier_mode": None,
        }
    ]


async def handle_sgo_fargate_webhook(
    *,
    body: Dict[str, Any],
    webhook_secret: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Process callback from ``run_sgo_worker.py``.

    DB jobs are updated by the worker during training; this handler syncs webhook metadata,
    optional status reconciliation, and external workflow resume URLs.
    """
    if not _verify_webhook_secret(webhook_secret):
        from fastapi import HTTPException

        raise HTTPException(status_code=401, detail="Invalid webhook secret")

    status = str(body.get("status") or "").upper()
    if status not in ("COMPLETED", "FAILED"):
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail="Invalid status")

    job_entries = _resolve_webhook_jobs(body)
    if not job_entries:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=400,
            detail="Invalid job_id or tier_results (no job ids to reconcile)",
        )

    audience_room_id = body.get("audience_room_id")
    pipeline_mode = str(body.get("pipeline_mode") or "")
    synced: List[str] = []

    for entry in job_entries:
        job_id = entry["job_id"]
        entry_status = str(entry.get("status") or status).upper()
        logger.info("[SGO_FARGATE_WEBHOOK] job_id=%s status=%s", job_id, entry_status)

        job = get_linkedin_room_pipeline_job(job_id) or {}
        if job.get("job_type") != JOB_TYPE_LINKEDIN_SGO_PIPELINE:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

        db_status = str(job.get("status") or "")
        if db_status != entry_status:
            jr = job.get("result") if isinstance(job.get("result"), dict) else {}
            update_linkedin_room_pipeline_job(
                job_id,
                status=entry_status,
                error=str(entry.get("error") or body.get("error") or job.get("error") or ""),
                result={
                    **jr,
                    "fargate_webhook_status": entry_status,
                    "fargate_webhook_synced": True,
                    "fargate_pipeline_mode": pipeline_mode or jr.get("fargate_pipeline_mode"),
                },
            )

        jr = job.get("result") if isinstance(job.get("result"), dict) else {}
        resume_url = (jr.get("workflow_resume_url") or "").strip()
        if resume_url:
            await _notify_workflow_resume(
                resume_url=resume_url,
                job_id=job_id,
                status=entry_status,
                audience_room_id=audience_room_id or job.get("audience_room_id"),
                error=str(entry.get("error") or body.get("error") or job.get("error") or ""),
                tier_results=body.get("tier_results") if isinstance(body.get("tier_results"), list) else None,
            )
        synced.append(job_id)

    primary_job_id = synced[0]
    if status == "COMPLETED":
        return {
            "message": "Job marked as complete. Pipeline may continue.",
            "job_id": primary_job_id,
            "synced_job_ids": synced,
        }
    return {
        "message": "Failure recorded.",
        "job_id": primary_job_id,
        "synced_job_ids": synced,
        "error": body.get("error"),
    }
