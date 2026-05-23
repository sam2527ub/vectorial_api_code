"""Run all SGO outer-iteration chunks inside one Fargate container (no Vercel HTTP chaining)."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.runtime_settings import get_runtime_settings
from app.services.linkedin_room_pipeline_async.linkedin_room_pipeline_job_repository import (
    JOB_TYPE_LINKEDIN_SGO_PIPELINE,
    get_linkedin_room_pipeline_job,
)
from app.services.linkedin_sgo_pipeline_service_async.linkedin_sgo_pipeline_async_worker import (
    run_linkedin_sgo_pipeline_job_chunk,
)

logger = logging.getLogger(__name__)


async def run_sgo_training_loop(
    *,
    job_id: str,
    audience_room_id: str,
    enterprise_name: Optional[str] = None,
    model: Optional[str] = None,
    force_resume: bool = False,
) -> Dict[str, Any]:
    """
    Process SGO chunks until the job reaches COMPLETED or FAILED.

    Uses ``workflow_orchestrated=True`` so chunks are not HTTP self-triggered on Vercel.
    Same ``job_id`` and S3 checkpoint layout as the serverless chunked path.
    """
    max_iters = max(1, int(get_runtime_settings().sgo_fargate.max_chunk_iterations))
    base_url = "http://127.0.0.1"  # unused when workflow_orchestrated=True

    for iteration in range(max_iters):
        job = get_linkedin_room_pipeline_job(job_id, enterprise_name=enterprise_name) or {}
        status = str(job.get("status") or "UNKNOWN")
        if status == "COMPLETED":
            logger.info(
                "[SGO_FARGATE_WORKER] job %s finished with status=%s after %s chunk(s)",
                job_id,
                status,
                iteration,
            )
            return {
                "job_id": job_id,
                "status": status,
                "error": job.get("error"),
                "result": job.get("result"),
            }
        if status == "FAILED" and not force_resume:
            logger.info(
                "[SGO_FARGATE_WORKER] job %s finished with status=%s after %s chunk(s)",
                job_id,
                status,
                iteration,
            )
            return {
                "job_id": job_id,
                "status": status,
                "error": job.get("error"),
                "result": job.get("result"),
            }
        if job.get("job_type") != JOB_TYPE_LINKEDIN_SGO_PIPELINE:
            return {
                "job_id": job_id,
                "status": "FAILED",
                "error": f"Invalid job type: {job.get('job_type')}",
            }
        if job.get("audience_room_id") != audience_room_id:
            return {
                "job_id": job_id,
                "status": "FAILED",
                "error": "audience_room_id mismatch",
            }

        await run_linkedin_sgo_pipeline_job_chunk(
            job_id=job_id,
            audience_room_id=audience_room_id,
            enterprise_name=enterprise_name,
            model=model,
            base_url=base_url,
            workflow_orchestrated=True,
            force_resume=force_resume,
        )

    job = get_linkedin_room_pipeline_job(job_id, enterprise_name=enterprise_name) or {}
    status = str(job.get("status") or "UNKNOWN")
    if status not in ("COMPLETED", "FAILED"):
        logger.error(
            "[SGO_FARGATE_WORKER] job %s hit max_chunk_iterations=%s; last status=%s",
            job_id,
            max_iters,
            status,
        )
        return {
            "job_id": job_id,
            "status": "FAILED",
            "error": f"Exceeded max_chunk_iterations ({max_iters})",
            "result": job.get("result"),
        }
    return {
        "job_id": job_id,
        "status": status,
        "error": job.get("error"),
        "result": job.get("result"),
    }
