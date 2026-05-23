"""Comment context summary endpoint.

Uses database (CommentContextSummaryJob table) for job tracking.
Uses chunked processing with self-trigger via HTTP so each chunk
runs in a new Vercel invocation and progress is persisted."""
import os
from typing import Optional
from fastapi import APIRouter, HTTPException, Query, BackgroundTasks, Request, Body
from pydantic import BaseModel, Field
from app.config import logger
from app.services.comment_context_summary_service import CommentContextSummaryHandler
from app.services.comment_context_summary_service.config import CommentContextSummaryConfig
from app.services.comment_context_summary_service.repositories.comment_context_summary_job_repository import (
    create_comment_context_summary_job,
    find_comment_context_summary_job_by_id,
)
from app.services.comment_context_summary_service.job_status_manager import JobStatusManager
from app.utils.helpers import ensure_db_available


class CreateCommentContextSummaryJobBody(BaseModel):
    """Request body for creating a comment context summary job."""

    audience_room_id: str = Field(..., alias="audienceRoomId", description="Audience room ID whose comments will be summarized")
    enterprise_name: Optional[str] = Field(None, alias="enterpriseName", description="Enterprise name for database routing")
    chunk_size: int = Field(
        CommentContextSummaryConfig().chunk_size_profiles, ge=1, le=50, alias="chunkSize", description="Profiles per chunk (1–50)"
    )

    model_config = {"populate_by_name": True}


class ProcessCommentContextSummaryChunkBody(BaseModel):
    """Request body for processing one chunk (internal / self-triggered)."""

    job_id: str = Field(..., alias="jobId", description="Job ID")
    audience_room_id: str = Field(..., alias="audienceRoomId", description="Audience room ID")
    start_profile_index: int = Field(0, alias="startProfileIndex", description="Profile index to start from")
    chunk_size: int = Field(
        CommentContextSummaryConfig().chunk_size_profiles, ge=1, le=50, alias="chunkSize", description="Profiles per chunk"
    )
    enterprise_name: Optional[str] = Field(None, alias="enterpriseName", description="Enterprise name for DB routing")

    model_config = {"populate_by_name": True}


def get_base_url(request: Optional[Request] = None) -> str:
    """Base URL for self-triggering process chunks."""
    if request is not None:
        try:
            return str(request.base_url).rstrip("/")
        except Exception:
            pass
    vercel_url = os.getenv("VERCEL_URL")
    if vercel_url:
        return f"https://{vercel_url}"
    return os.getenv("COMMENT_CONTEXT_SUMMARY_BASE_URL", "https://vectorial-reddit-pipeline.vercel.app")


router = APIRouter()


async def _trigger_process_endpoint(
    base_url: str,
    job_id: str,
    audience_room_id: str,
    enterprise_name: Optional[str],
    chunk_size: int,
) -> None:
    """Call the process endpoint so the first chunk runs in a new invocation."""
    import httpx
    url = f"{base_url.rstrip('/')}/api/v1/comment-context-summary/process"
    json_body = {
        "jobId": job_id,
        "audienceRoomId": audience_room_id,
        "startProfileIndex": 0,
        "chunkSize": chunk_size,
    }
    if enterprise_name is not None:
        json_body["enterpriseName"] = enterprise_name
    try:
        # Fire-and-forget: we only need to invoke /process; don't wait for the first chunk to finish.
        # Use a short read timeout so we don't fail when the first chunk takes >120s (cold start + load + process).
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=json_body)
            response.raise_for_status()
        logger.info(f"Triggered process endpoint for job {job_id}")
    except httpx.ReadTimeout:
        # Request was sent; server is likely still processing the first chunk. Don't treat as failure.
        logger.info(
            f"Process endpoint triggered for job {job_id} (read timeout awaiting response; chunk may still be running)"
        )
    except Exception as e:
        logger.error(
            f"Failed to trigger process for job {job_id}: type={type(e).__name__} msg={e!r}",
            exc_info=True,
        )


@router.post("/api/v1/comment-context-summary")
async def create_comment_context_summary_job_endpoint(
    request: Request,
    background_tasks: BackgroundTasks,
    body: CreateCommentContextSummaryJobBody = Body(..., embed=False),
) -> dict:
    """
    Create a comment context summary job and return job_id immediately.
    Processing runs in chunks via the /process endpoint (each chunk in a new request).
    """
    # Use body parameters
    audience_room_id = body.audience_room_id
    enterprise_name = body.enterprise_name or "default"
    chunk_size = body.chunk_size or CommentContextSummaryConfig().chunk_size_profiles
    
    logger.debug(
        f"POST comment-context-summary audienceRoomId={audience_room_id} enterprise={enterprise_name} chunkSize={chunk_size}"
    )

    try:
        ensure_db_available("audience")
        handler = CommentContextSummaryHandler()

        if not handler.storage_client or not handler.storage_client.is_configured():
            raise HTTPException(
                status_code=503,
                detail="Storage client not configured. Please set AWS credentials."
            )

        # Validate scraping is complete - use the resolved enterprise_name
        from app.services.comment_context_summary_service.utils import get_comment_context_meta_key
        meta_key = get_comment_context_meta_key(enterprise_name, audience_room_id)
        try:
            meta = handler.storage_client.read_json(meta_key)
            if meta.get("status") != "scraping_complete":
                raise HTTPException(
                    status_code=400,
                    detail="Job scraping not complete. Poll comment-context status first.",
                )
        except Exception as e:
            if isinstance(e, HTTPException):
                raise
            raise HTTPException(
                status_code=404,
                detail=f"No scraping job found for audience room {audience_room_id}. Run comment-context/start first.",
            )

        # Get total profiles for job tracking
        from app.database.repositories.shared_repositories.audience_room_repository import find_audience_room_by_id
        room = find_audience_room_by_id(
            audience_room_id,
            include_profiles=True,
            enterprise_name=enterprise_name,
        )
        if not room or not room.profiles:
            raise HTTPException(status_code=404, detail="Room or profiles not found.")

        job = create_comment_context_summary_job(
            audience_room_id=audience_room_id,
            enterprise_name=enterprise_name
        )

        # Update total profiles
        JobStatusManager.update_total_profiles(job.id, len(room.profiles), enterprise_name)

        base_url = get_base_url(request)
        background_tasks.add_task(
            _trigger_process_endpoint,
            base_url,
            job.id,
            audience_room_id,
            enterprise_name,
            chunk_size,  # Use resolved chunk_size
        )

        logger.info(f"Comment context summary job created: {job.id}")

        return {
            "job_id": job.id,
            "status": "PENDING",
            "message": "Job created successfully. Use GET endpoint to check status.",
        }

    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Validation error in comment context summary: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating comment context summary job: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create comment context summary job: {str(e)}"
        )

@router.post("/api/v1/comment-context-summary/process")
async def process_comment_context_summary_chunk(
    request: Request,
    body: ProcessCommentContextSummaryChunkBody = Body(..., embed=False),
) -> dict:
    """
    Process one chunk of profiles (internal, self-triggered by previous chunk).
    Runs in its own request so Vercel doesn't time out.
    """
    # Use body parameters
    audience_room_id = body.audience_room_id
    enterprise_name = body.enterprise_name or "beta"
    
    logger.debug(f"Process chunk: job={body.job_id}, startProfileIndex={body.start_profile_index}")
    ensure_db_available("audience")
    base_url = get_base_url(request)
    handler = CommentContextSummaryHandler()
    await handler.process_job_chunk(
        job_id=body.job_id,
        audience_room_id=audience_room_id,
        enterprise_name=enterprise_name,
        start_profile_index=body.start_profile_index,
        base_url=base_url,
        chunk_size=body.chunk_size,
    )
    return {
        "job_id": body.job_id,
        "status": "PROCESSING",
        "message": f"Chunk starting at profile {body.start_profile_index} processed",
    }


@router.get("/api/v1/comment-context-summary/{job_id}")
async def get_comment_context_summary_job_status(
    job_id: str,
    enterpriseName: Optional[str] = Query(None, description="Enterprise name for database routing")
) -> dict:
    """
    Get the status of a comment context summary job.
    
    Returns:
        Dict with job status, progress, and results (if completed)
    """
    logger.debug(f"GET comment-context-summary status {job_id}")
    ensure_db_available("audience")
    
    try:
        job = find_comment_context_summary_job_by_id(
            job_id=job_id,
            enterprise_name=enterpriseName
        )
        
        if not job:
            raise HTTPException(
                status_code=404,
                detail=f"Job {job_id} not found"
            )
        
        response = {
            "job_id": job.id,
            "audience_room_id": job.audience_room_id,
            "status": job.status,
            "total_profiles": job.total_profiles,
            "processed_profiles": job.processed_profiles,
            "total_comments": job.total_comments,
            "enriched_comments": job.enriched_comments,
            "skipped_comments": job.skipped_comments,
            "failed_comments": job.failed_comments,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "updated_at": job.updated_at.isoformat() if job.updated_at else None,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        }
        
        if job.error:
            response["error"] = job.error
        
        if job.result:
            response["result"] = job.result
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching job status: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch job status: {str(e)}"
        )
