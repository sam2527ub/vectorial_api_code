"""Async comment context summary endpoints for long-running Groq jobs on LinkedIn comments."""

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Path, Query, Request

from app.config import logger
from app.utils.helpers import ensure_db_available
from app.services.comment_context_summary_service.handler import CommentContextSummaryAsyncHandler
from app.services.comment_context_summary_service.repositories.comment_context_summary_job_repository import (
    get_pending_comment_context_summary_jobs,
)
from app.services.comment_context_summary_service.config import CommentContextSummaryConfig
from app.services.user_profile_summarization_service.utils import get_base_url


router = APIRouter()

CONFIG = CommentContextSummaryConfig()


@router.post("/api/v1/audience-rooms/{audience_room_id}/comment-context-summary/async")
async def trigger_async_comment_context_summary(
    audience_room_id: str = Path(...),
    enterpriseName: Optional[str] = Query(None),
    chunkSizeProfiles: int = Query(
        CONFIG.chunk_size_profiles, ge=1, le=50, description="Profiles per chunk"
    ),
    background_tasks: BackgroundTasks = None,
    request: Request = None,
):
    """
    Trigger async comment context summaries that run in the background.

    Returns immediately with a job_id for polling status.
    """
    logger.info(
        "=== ASYNC COMMENT CONTEXT SUMMARY TRIGGER START (room=%s, enterprise=%s, chunkSizeProfiles=%s) ===",
        audience_room_id,
        enterpriseName,
        chunkSizeProfiles,
    )

    ensure_db_available("audience")
    handler = CommentContextSummaryAsyncHandler(config=CONFIG)

    result = handler.trigger_async_comment_context_summary(
        audience_room_id=audience_room_id,
        enterprise_name=enterpriseName,
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
        handler.process_comment_context_summary_chunks,
        job_id=job_id,
        audience_room_id=audience_room_id,
        enterprise_name=enterpriseName,
        chunk_size_profiles=chunkSizeProfiles,
        start_chunk=0,
        base_url=base_url,
    )

    logger.info(
        "=== ASYNC COMMENT CONTEXT SUMMARY TRIGGERED: job_id=%s ===",
        job_id,
    )
    return result


@router.post("/api/v1/audience-rooms/{audience_room_id}/comment-context-summary/async/process")
async def process_comment_context_summary_chunks_endpoint(
    audience_room_id: str = Path(...),
    jobId: str = Query(..., description="Job ID to continue processing"),
    startChunk: int = Query(0, description="Chunk number to start from"),
    chunkSizeProfiles: int = Query(
        CONFIG.chunk_size_profiles,
        description="Profiles per chunk",
    ),
    enterpriseName: Optional[str] = Query(None),
    background_tasks: BackgroundTasks = None,
    request: Request = None,
):
    """Internal endpoint to process next batch of comment-context-summary chunks (self-triggered)."""
    logger.info(
        "Processing comment context summary chunks for job %s starting from chunk %s",
        jobId,
        startChunk,
    )

    base_url = get_base_url()
    if request:
        try:
            base_url = str(request.base_url).rstrip("/")
        except Exception:
            pass

    handler = CommentContextSummaryAsyncHandler(config=CONFIG)
    if background_tasks is None:
        raise HTTPException(
            status_code=500,
            detail="BackgroundTasks not available; cannot continue async job.",
        )

    background_tasks.add_task(
        handler.process_comment_context_summary_chunks,
        job_id=jobId,
        audience_room_id=audience_room_id,
        enterprise_name=enterpriseName,
        chunk_size_profiles=chunkSizeProfiles,
        start_chunk=startChunk,
        base_url=base_url,
    )

    return {
        "job_id": jobId,
        "status": "PROCESSING",
        "message": f"Processing comment context summary chunks starting from {startChunk}",
    }


@router.get("/api/v1/audience-rooms/{audience_room_id}/comment-context-summary/async/status/{job_id}")
async def get_async_comment_context_summary_status(
    audience_room_id: str = Path(...),
    job_id: str = Path(..., description="Job ID returned from trigger endpoint"),
    enterpriseName: Optional[str] = Query(None),
):
    """Check the status of an async comment context summary job from DATABASE."""
    logger.info(
        "Checking async comment context summary status for job_id=%s (room=%s)",
        job_id,
        audience_room_id,
    )
    handler = CommentContextSummaryAsyncHandler(config=CONFIG)
    return handler.get_job_status(
        job_id=job_id,
        audience_room_id=audience_room_id,
        enterprise_name=enterpriseName,
    )


@router.get("/api/v1/comment-context-summary/async/pending")
async def get_pending_comment_context_summary_jobs_endpoint(
    enterpriseName: Optional[str] = Query(None, description="Enterprise name"),
):
    """Get all pending/processing comment context summary jobs (for debugging/admin)."""
    jobs = get_pending_comment_context_summary_jobs(enterprise_name=enterpriseName)
    return {"jobs": jobs, "count": len(jobs)}

