"""Post dimension tagging endpoint.

Uses database (PostDimensionTaggingJob table) for job tracking.
Uses chunked processing with self-trigger via HTTP (summaries-style) so each chunk
runs in a new Vercel invocation and progress is persisted."""
import os
from typing import Optional
from fastapi import APIRouter, HTTPException, Query, BackgroundTasks, Request, Body
from pydantic import BaseModel, Field
from app.config import logger
from app.services.post_dimension_tagging_service import PostDimensionTaggingHandler
from app.services.post_dimension_tagging_service.config import CHUNK_SIZE_PROFILES
from app.services.post_dimension_tagging_service.repositories.post_dimension_tagging_job_repository import (
    create_post_dimension_tagging_job,
    find_post_dimension_tagging_job_by_id,
)


class CreateDimensionTaggingJobBody(BaseModel):
    """Request body for creating a dimension tagging job."""

    audience_room_id: str = Field(..., alias="audienceRoomId", description="Audience room ID whose posts will be tagged")
    enterprise_name: Optional[str] = Field(None, alias="enterpriseName", description="Enterprise name for database routing")
    chunk_size: int = Field(
        CHUNK_SIZE_PROFILES, ge=1, le=50, alias="chunkSize", description="Profiles per chunk (1–50)"
    )

    model_config = {"populate_by_name": True}


class ProcessDimensionTaggingChunkBody(BaseModel):
    """Request body for processing one chunk (internal / self-triggered)."""

    job_id: str = Field(..., alias="jobId", description="Job ID")
    audience_room_id: str = Field(..., alias="audienceRoomId", description="Audience room ID")
    start_profile_index: int = Field(0, alias="startProfileIndex", description="Profile index to start from")
    chunk_size: int = Field(
        CHUNK_SIZE_PROFILES, ge=1, le=50, alias="chunkSize", description="Profiles per chunk"
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
    return os.getenv("DIMENSION_TAGGING_BASE_URL", "https://vectorial-reddit-pipeline.vercel.app")


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
    url = f"{base_url.rstrip('/')}/api/v1/posts/dimension-tagging/process"
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


@router.post("/api/v1/posts/dimension-tagging")
async def create_dimension_tagging_job(
    request: Request,
    background_tasks: BackgroundTasks,
    body: CreateDimensionTaggingJobBody = Body(..., embed=False),
) -> dict:
    """
    Create a dimension tagging job and return job_id immediately.
    Processing runs in chunks via the /process endpoint (each chunk in a new request).
    """
    logger.debug(
        f"POST dimension-tagging audienceRoomId={body.audience_room_id} enterprise={body.enterprise_name or 'default'}"
    )

    try:
        handler = PostDimensionTaggingHandler()

        if not handler.storage_client or not handler.storage_client.is_configured():
            raise HTTPException(
                status_code=503,
                detail="Storage client not configured. Please set AWS credentials."
            )

        job = create_post_dimension_tagging_job(
            audience_room_id=body.audience_room_id,
            enterprise_name=body.enterprise_name
        )

        base_url = get_base_url(request)
        background_tasks.add_task(
            _trigger_process_endpoint,
            base_url,
            job.id,
            body.audience_room_id,
            body.enterprise_name,
            body.chunk_size,
        )

        logger.info(f"Dimension tagging job created: {job.id}")

        return {
            "job_id": job.id,
            "status": "PENDING",
            "message": "Job created successfully. Use GET endpoint to check status."
        }

    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Validation error in dimension tagging: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating dimension tagging job: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create dimension tagging job: {str(e)}"
        )


@router.post("/api/v1/posts/dimension-tagging/process")
async def process_dimension_tagging_chunk(
    request: Request,
    body: ProcessDimensionTaggingChunkBody = Body(..., embed=False),
) -> dict:
    """
    Process one chunk of profiles (internal, self-triggered by previous chunk).
    Runs in its own request so Vercel doesn't time out.
    """
    logger.debug(f"Process chunk: job={body.job_id}, startProfileIndex={body.start_profile_index}")
    base_url = get_base_url(request)
    handler = PostDimensionTaggingHandler()
    await handler.process_job_chunk(
        job_id=body.job_id,
        audience_room_id=body.audience_room_id,
        enterprise_name=body.enterprise_name,
        start_profile_index=body.start_profile_index,
        base_url=base_url,
        chunk_size=body.chunk_size,
    )
    return {
        "job_id": body.job_id,
        "status": "PROCESSING",
        "message": f"Chunk starting at profile {body.start_profile_index} processed",
    }


@router.get("/api/v1/posts/dimension-tagging/{job_id}")
async def get_dimension_tagging_job_status(
    job_id: str,
    enterpriseName: Optional[str] = Query(None, description="Enterprise name for database routing")
) -> dict:
    """
    Get the status of a dimension tagging job.
    
    Returns:
        Dict with job status, progress, and results (if completed)
    """
    logger.debug(f"GET dimension-tagging status {job_id}")
    
    try:
        job = find_post_dimension_tagging_job_by_id(
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
            "total_posts": job.total_posts,
            "processed_posts": job.processed_posts,
            "tagged_posts": job.tagged_posts,
            "failed_posts": job.failed_posts,
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
