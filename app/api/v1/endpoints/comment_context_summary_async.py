"""Async comment context summary endpoints for long-running Groq jobs on LinkedIn comments.

Uses self-trigger via HTTP so each chunk runs in its own request/invocation (Vercel-friendly).
"""
from typing import Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Path, Query, Request

from app.config import logger
from app.utils.helpers import ensure_db_available
from app.services.comment_context_summary_service.comment_context_summary_async_handler import CommentContextSummaryAsyncHandler
from app.services.comment_context_summary_service.config import CommentContextSummaryConfig
from app.services.user_profile_summarization_service.utils import get_base_url


router = APIRouter()

CONFIG = CommentContextSummaryConfig()


async def _trigger_process_endpoint(
    base_url: str,
    job_id: str,
    audience_room_id: str,
    enterprise_name: Optional[str],
    chunk_size_profiles: int,
) -> None:
    """Call the process endpoint so the first chunk runs in a new request/invocation."""
    url = (
        f"{base_url.rstrip('/')}/api/v1/audience-rooms/"
        f"{audience_room_id}/comment-context-summary/async/process"
    )
    params = {
        "jobId": job_id,
        "startChunk": 0,
        "chunkSizeProfiles": chunk_size_profiles,
    }
    if enterprise_name is not None:
        params["enterpriseName"] = enterprise_name
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, params=params)
            response.raise_for_status()
        logger.info("[CommentContextSummary] Triggered process endpoint for job %s", job_id)
    except httpx.ReadTimeout:
        logger.info(
            "[CommentContextSummary] Process endpoint triggered for job %s (read timeout; chunk may still be running)",
            job_id,
        )
    except Exception as e:
        logger.error(
            "[CommentContextSummary] Failed to trigger process for job %s: %s",
            job_id,
            e,
            exc_info=True,
        )


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
        _trigger_process_endpoint,
        base_url,
        job_id,
        audience_room_id,
        enterpriseName,
        chunkSizeProfiles,
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
    request: Request = None,
):
    """
    Process one chunk of comment-context-summary (self-triggered by trigger or previous chunk).
    Runs inside this request so each chunk uses its own invocation (avoids Vercel timeout).
    """
    logger.info(
        "Processing comment context summary chunks for job %s starting from chunk %s",
        jobId,
        startChunk,
    )

    ensure_db_available("audience")
    base_url = get_base_url()
    if request:
        try:
            base_url = str(request.base_url).rstrip("/")
        except Exception:
            pass

    # One chunk per invocation so each request stays within timeout
    process_config = CommentContextSummaryConfig()
    process_config.chunks_per_api_call = 1
    handler = CommentContextSummaryAsyncHandler(config=process_config)

    await handler.process_comment_context_summary_chunks(
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

