"""Async Profile Summaries endpoints for long-running summary generation jobs.

Uses User Profile Summarization service. Job state is stored in DB (SummariesJob table).
Uses self-triggering chunk processing to avoid Vercel 13-minute timeout.
"""
from typing import Optional
from fastapi import APIRouter, HTTPException, Path, Query, BackgroundTasks, Request

from app.config import s3_client, s3_bucket, logger
from app.utils.helpers import ensure_db_available
from app.services.user_profile_summarization_service import (
    request_handler as summarization_handler,
    CHUNK_SIZE,
)
from app.services.user_profile_summarization_service.utils import get_base_url

router = APIRouter()


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
    Trigger async profile summaries generation that runs in the background.
    Returns immediately with a job_id for polling status.

    Job state is stored in DATABASE (SummariesJob table) - survives Vercel cold starts.
    Uses self-triggering chunk processing to avoid Vercel 13-minute timeout.
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

    background_tasks.add_task(
        summarization_handler.process_summaries_chunks,
        job_id=job_id,
        audience_room_id=audience_room_id,
        enterprise_name=enterpriseName,
        chunk_size=chunkSize,
        start_chunk=0,
        base_url=base_url,
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
    background_tasks: BackgroundTasks = None,
    request: Request = None,
):
    """Internal endpoint to process next batch of chunks (self-triggered)."""
    logger.info(f"Processing chunks for job {jobId} starting from chunk {startChunk}")

    base_url = get_base_url()
    if request:
        try:
            base_url = str(request.base_url).rstrip("/")
        except Exception:
            pass

    background_tasks.add_task(
        summarization_handler.process_summaries_chunks,
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
