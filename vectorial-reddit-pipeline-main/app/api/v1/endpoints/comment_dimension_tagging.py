"""Comment dimension tagging endpoint.
Refactored for sequential stability to avoid OpenAI 429 errors.
"""
import os
import math
from typing import Optional
from fastapi import APIRouter, HTTPException, Query, BackgroundTasks, Request, Body
from pydantic import BaseModel, Field
from app.config import logger
from app.services.comment_dimension_tagging_service.comment_dimension_tagging_handler import CommentDimensionTaggingHandler
from app.services.comment_dimension_tagging_service.repositories.comment_dimension_tagging_job_repository import (
    create_comment_dimension_tagging_job,
    find_comment_dimension_tagging_job_by_id,
    update_job_status
)
from app.services.comment_dimension_tagging_service.repositories.audience_room_repository import find_audience_room_with_category


class CreateCommentDimensionTaggingJobBody(BaseModel):
    audience_room_id: str = Field(..., alias="audienceRoomId")
    enterprise_name: Optional[str] = Field(None, alias="enterpriseName")
    chunk_size: Optional[int] = Field(None, alias="chunkSize")
    model_config = {"populate_by_name": True}


class ProcessCommentDimensionTaggingChunkBody(BaseModel):
    job_id: str = Field(..., alias="jobId")
    audience_room_id: str = Field(..., alias="audienceRoomId")
    start_profile_index: int = Field(0, alias="startProfileIndex")
    chunk_size: int = Field(..., alias="chunkSize")
    enterprise_name: Optional[str] = Field(None, alias="enterpriseName")
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
    url = f"{base_url.rstrip('/')}/api/v1/comments/dimension-tagging/process"
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


@router.post("/api/v1/comments/dimension-tagging")
async def create_comment_dimension_tagging_job_endpoint(
    request: Request,
    background_tasks: BackgroundTasks,
    body: CreateCommentDimensionTaggingJobBody = Body(..., embed=False),
) -> dict:
    try:
        handler = CommentDimensionTaggingHandler()
        handler._validate()

        job = create_comment_dimension_tagging_job(
            audience_room_id=body.audience_room_id,
            enterprise_name=body.enterprise_name
        )

        audience_room = await find_audience_room_with_category(
            body.audience_room_id,
            enterprise_name=body.enterprise_name,
            include_profiles=True
        )
        
        if not audience_room:
            raise HTTPException(status_code=404, detail="Audience room not found")

        total_profiles = len(audience_room.profiles or [])
        chunk_size = body.chunk_size or handler.calculate_chunk_size(total_profiles)
        total_chunks = math.ceil(total_profiles / chunk_size) if chunk_size > 0 else 0

        update_job_status(
            job_id=job.id,
            total_chunks=total_chunks,
            chunk_size_profiles=chunk_size,
            enterprise_name=body.enterprise_name
        )

        base_url = get_base_url(request)
        background_tasks.add_task(
            _trigger_process_endpoint,
            base_url,
            job.id,
            body.audience_room_id,
            body.enterprise_name,
            chunk_size,
        )

        return {
            "job_id": job.id,
            "status": "PENDING",
            "message": "Job created successfully. Use GET endpoint to check status."
        }

    except Exception as e:
        logger.error(f"Error creating job: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/v1/comments/dimension-tagging/process", include_in_schema=False)
async def process_comment_dimension_tagging_chunk(
    request: Request,
    body: ProcessCommentDimensionTaggingChunkBody = Body(..., embed=False),
) -> dict:
    base_url = get_base_url(request)
    handler = CommentDimensionTaggingHandler()
    
    # FIXED: Corrected the typo in body.chunk_size
    await handler.process_job_chunk(
        job_id=body.job_id,
        audience_room_id=body.audience_room_id,
        enterprise_name=body.enterprise_name,
        start_profile_index=body.start_profile_index,
        base_url=base_url,
        chunk_size=body.chunk_size,
    )
    return {"job_id": body.job_id, "status": "PROCESSING"}
@router.get("/api/v1/comments/dimension-tagging/{job_id}")
async def get_comment_dimension_tagging_job_status(
    job_id: str,
    enterpriseName: Optional[str] = Query(None, description="Enterprise name for database routing")
) -> dict:
    """Get the status of a comment dimension tagging job."""
    logger.debug(f"GET comment-dimension-tagging status {job_id}")
    
    try:
        job = find_comment_dimension_tagging_job_by_id(
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
            "total_comments": job.total_comments,
            "processed_comments": job.processed_comments,
            "tagged_comments": job.tagged_comments,
            "failed_comments": job.failed_comments,
            "skipped_comments": job.skipped_comments,
            "total_chunks": job.total_chunks,
            "completed_chunks": job.completed_chunks,
            "chunk_size_profiles": job.chunk_size_profiles,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "updated_at": job.updated_at.isoformat() if job.updated_at else None,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        }
        
        if job.error:
            response["error"] = job.error
        
        if job.result:
            response["result"] = job.result
        
        if job.metadata:
            response["metadata"] = job.metadata
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching job status: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch job status: {str(e)}"
        )
