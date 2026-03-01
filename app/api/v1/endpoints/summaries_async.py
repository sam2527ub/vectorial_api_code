"""Async Profile Summaries endpoints for long-running summary generation jobs.

Uses User Profile Summarization service. Job state is stored in DB (SummariesJob table).
Uses self-trigger via HTTP so each chunk runs in its own request/invocation (Vercel-friendly).
"""
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Path, Query, BackgroundTasks, Request

from app.config import s3_client, s3_bucket, logger
from app.utils.helpers import ensure_db_available
from app.services.user_profile_summarization_service import (
    request_handler as summarization_handler,
    CHUNK_SIZE,
)
from app.services.user_profile_summarization_service.config import UserProfileSummarizationConfig
from app.services.user_profile_summarization_service.user_profile_summarization_handler import UserProfileSummarizationHandler
from app.services.user_profile_summarization_service.utils import get_base_url

router = APIRouter()


async def _trigger_process_endpoint(
    base_url: str,
    job_id: str,
    audience_room_id: str,
    enterprise_name: Optional[str],
    chunk_size: int,
) -> None:
    """Call the process endpoint so the first chunk runs in a new request/invocation."""
    url = (
        f"{base_url.rstrip('/')}/api/v1/audience-rooms/"
        f"{audience_room_id}/generate-summaries/async/process"
    )
    params = {
        "jobId": job_id,
        "startChunk": 0,
        "chunkSize": chunk_size,
    }
    if enterprise_name is not None:
        params["enterpriseName"] = enterprise_name
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, params=params)
            response.raise_for_status()
        logger.info("[SummariesJob] Triggered process endpoint for job %s", job_id)
    except httpx.ReadTimeout:
        logger.info(
            "[SummariesJob] Process endpoint triggered for job %s (read timeout; chunk may still be running)",
            job_id,
        )
    except Exception as e:
        logger.error(
            "[SummariesJob] Failed to trigger process for job %s: %s",
            job_id,
            e,
            exc_info=True,
        )


@router.post("/api/v1/audience-rooms/{audience_room_id}/generate-summaries/async")
async def trigger_async_summaries(
    audience_room_id: str = Path(...),
    enterpriseName: Optional[str] = Query(None),
    chunkSize: int = Query(CHUNK_SIZE, ge=1, le=20),
    taskToken: Optional[str] = Query(None),
    background_tasks: BackgroundTasks = None,
    request: Request = None,
):
    """
    Trigger async profile summaries generation.
    Returns immediately with a job_id for polling status.
    First chunk runs in a new request via HTTP call to /process (Vercel-friendly).
    """
    logger.info(f"=== ASYNC SUMMARIES TRIGGER START ===")
    logger.info(f"Request: room_id={audience_room_id}, enterprise={enterpriseName}, chunkSize={chunkSize}")

    ensure_db_available("audience")
    from app.config import openai_client
    if not openai_client:
        raise HTTPException(status_code=503, detail="OpenAI client not initialized. Please set OPENAI_API_KEY.")
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured.")

    result = summarization_handler.trigger_async_summaries(
        audience_room_id=audience_room_id,
        enterprise_name=enterpriseName,
        chunk_size=chunkSize,
        task_token=taskToken,
    )
    job_id = result["job_id"]
    base_url = get_base_url()
    if request:
        try:
            base_url = str(request.base_url).rstrip("/")
        except Exception:
            pass

    if background_tasks is None:
        raise HTTPException(
            status_code=500,
            detail="BackgroundTasks not available; cannot start async job.",
        )

    background_tasks.add_task(
        _trigger_process_endpoint,
        base_url,
        job_id,
        audience_room_id,
        enterpriseName,
        chunkSize,
    )

    logger.info(f"=== ASYNC SUMMARIES TRIGGERED: job_id={job_id} ===")
    return result


@router.post("/api/v1/audience-rooms/{audience_room_id}/generate-summaries/async/process")
async def process_summaries_chunks_endpoint(
    audience_room_id: str = Path(...),
    jobId: str = Query(..., description="Job ID to continue processing"),
    startChunk: int = Query(0, description="Chunk number to start from"),
    chunkSize: int = Query(CHUNK_SIZE, description="Profiles per chunk"),
    enterpriseName: Optional[str] = Query(None),
    request: Request = None,
):
    """
    Process one chunk of profile summaries (self-triggered by trigger or previous chunk).
    Runs inside this request so each chunk uses its own invocation (avoids Vercel timeout).
    """
    logger.info(f"Processing chunks for job {jobId} starting from chunk {startChunk}")

    ensure_db_available("audience")
    base_url = get_base_url()
    if request:
        try:
            base_url = str(request.base_url).rstrip("/")
        except Exception:
            pass

    # One chunk per invocation so each request stays within timeout
    process_config = UserProfileSummarizationConfig(
        chunk_size=chunkSize,
        chunks_per_call=1,
    )
    handler = UserProfileSummarizationHandler(config=process_config)

    await handler.process_summaries_chunks(
        job_id=jobId,
        audience_room_id=audience_room_id,
        enterprise_name=enterpriseName,
        chunk_size=chunkSize,
        start_chunk=startChunk,
        base_url=base_url,
    )

    return {
        "job_id": jobId,
        "status": "PROCESSING",
        "message": f"Processing chunks starting from {startChunk}",
    }


@router.get("/api/v1/audience-rooms/{audience_room_id}/generate-summaries/async/status/{job_id}")
async def get_async_summaries_status(
    audience_room_id: str = Path(...),
    job_id: str = Path(..., description="Job ID returned from trigger endpoint"),
    enterpriseName: Optional[str] = Query(None),
):
    """Check the status of an async profile summaries job from DATABASE."""
    logger.info(f"Checking async summaries status for job_id={job_id}")
    return summarization_handler.get_job_status(
        job_id=job_id,
        audience_room_id=audience_room_id,
        enterprise_name=enterpriseName,
    )


@router.get("/api/v1/summaries/async/pending")
async def get_pending_summaries_jobs_endpoint(
    enterpriseName: Optional[str] = Query(None, description="Enterprise name"),
):
    """Get all pending/processing summaries jobs."""
    jobs = summarization_handler.get_pending_jobs(enterprise_name=enterpriseName)
    return {"jobs": jobs, "count": len(jobs)}
