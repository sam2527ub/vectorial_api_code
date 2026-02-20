"""Async Classifier endpoints for long-running classification jobs.

Uses User Post Classifier service. Job state is stored in DB (ClassifierJob table).
"""
from typing import Optional
from fastapi import APIRouter, HTTPException, Path, Query, BackgroundTasks
from pydantic import BaseModel

from app.config import logger
from app.services.user_post_classifier_service import request_handler as user_post_classifier_handler

router = APIRouter()


class AsyncClassifierRequest(BaseModel):
    """Request model for async classifier trigger."""
    audienceRoomId: str
    classifierId: str
    enterpriseName: Optional[str] = None
    batchSize: int = 10
    taskToken: Optional[str] = None


@router.post("/api/classifier/async/run")
async def trigger_async_classifier(
    payload: AsyncClassifierRequest,
    background_tasks: BackgroundTasks,
):
    """
    Trigger async classifier job that runs in the background.
    Returns immediately with a job_id for polling status.

    Job state is stored in DATABASE (ClassifierJob table) - survives Vercel cold starts.

    Request Body:
    - audienceRoomId: UUID of the audience room
    - classifierId: UUID of the classifier to use
    - enterpriseName (optional): Enterprise name for database selection
    - batchSize (optional): Number of profiles per batch (default: 10)
    - taskToken (optional): Step Functions callback token
    """
    logger.info(f"=== ASYNC CLASSIFIER TRIGGER START ===")
    logger.info(f"Request: classifier_id={payload.classifierId}, room_id={payload.audienceRoomId}, enterprise={payload.enterpriseName}")

    try:
        result = user_post_classifier_handler.trigger_async_classifier(
            audience_room_id=payload.audienceRoomId,
            classifier_id=payload.classifierId,
            enterprise_name=payload.enterpriseName,
            batch_size=payload.batchSize,
            task_token=payload.taskToken,
        )
        job_id = result["job_id"]
        background_tasks.add_task(
            user_post_classifier_handler.process_classifier_job,
            job_id=job_id,
            audience_room_id=payload.audienceRoomId,
            classifier_id=payload.classifierId,
            enterprise_name=payload.enterpriseName,
            batch_size=payload.batchSize,
        )
        logger.info(f"=== ASYNC CLASSIFIER TRIGGERED: job_id={job_id} ===")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in trigger_async_classifier: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/classifier/async/status/{job_id}")
async def get_async_classifier_status(
    job_id: str = Path(..., description="Job ID returned from trigger endpoint"),
    enterpriseName: Optional[str] = Query(None, description="Enterprise name"),
):
    """
    Check the status of an async classifier job from DATABASE.

    Returns current progress including:
    - status: PENDING, PROCESSING, COMPLETED, FAILED
    - total_profiles: Total profiles to process
    - processed_profiles: Number of profiles processed so far
    - total_posts_classified: Total posts classified
    """
    logger.info(f"Checking async classifier status for job_id={job_id}")
    try:
        return user_post_classifier_handler.get_job_status(
            job_id=job_id,
            enterprise_name=enterpriseName,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in get_async_classifier_status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/classifier/async/pending")
async def get_pending_classifier_jobs_endpoint(
    enterpriseName: Optional[str] = Query(None, description="Enterprise name"),
):
    """Get all pending/processing classifier jobs."""
    jobs = user_post_classifier_handler.get_pending_jobs(enterprise_name=enterpriseName)
    return {"jobs": jobs, "count": len(jobs)}
