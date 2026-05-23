"""API endpoints for Reddit Workflow Job status tracking."""
import logging
from typing import Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.database.repositories.shared_repositories import (
    find_reddit_workflow_job_by_job_id,
    create_reddit_workflow_job,
    update_reddit_workflow_job_progress,
    update_reddit_workflow_job_run_id,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["workflow-jobs"],
)


class CreateWorkflowJobRequest(BaseModel):
    """Request body for creating a workflow job."""
    job_id: str
    user_id: str
    input_data: Dict[str, Any]
    run_id: Optional[str] = None


class UpdateWorkflowJobRequest(BaseModel):
    """Request body for updating workflow job progress."""
    status: Optional[str] = None
    current_step: Optional[str] = None
    progress_percentage: Optional[int] = None
    step_details: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    completed: bool = False


@router.post("/api/v1/workflow-jobs/create")
async def create_workflow_job(
    request: CreateWorkflowJobRequest,
    enterpriseName: Optional[str] = Query(None, description="Enterprise name for database routing")
):
    """Create or update a Reddit workflow job record with run_id."""
    try:
        logger.info(f"Creating/updating workflow job: job_id={request.job_id}, user_id={request.user_id}, run_id={request.run_id}, enterprise={enterpriseName}")
        
        # Check if job already exists
        existing_job = find_reddit_workflow_job_by_job_id(request.job_id, enterprise_name=enterpriseName)
        
        if existing_job:
            # Job exists - just update run_id if provided
            if request.run_id:
                logger.info(f"Job {request.job_id} already exists, updating run_id to {request.run_id}")
                job = update_reddit_workflow_job_run_id(
                    job_id=request.job_id,
                    run_id=request.run_id,
                    enterprise_name=enterpriseName
                )
                if not job:
                    raise HTTPException(status_code=404, detail=f"Failed to update workflow job: {request.job_id}")
                return {
                    "status": "success",
                    "message": "Workflow job updated with run_id",
                    "job": job.to_dict()
                }
            else:
                return {
                    "status": "success",
                    "message": "Workflow job already exists",
                    "job": existing_job.to_dict()
                }
        else:
            job = create_reddit_workflow_job(
                job_id=request.job_id,
                user_id=request.user_id,
                input_data=request.input_data,
                enterprise_name=enterpriseName,
                run_id=request.run_id
            )
            
            return {
                "status": "success",
                "message": "Workflow job created",
                "job": job.to_dict()
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating/updating workflow job: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/v1/workflow-jobs/by-job-id/{job_id}")
async def get_workflow_job_by_job_id(
    job_id: str,
    enterpriseName: Optional[str] = Query(None, description="Enterprise name for database routing")
):
    """Get a Reddit workflow job by its job_id."""
    try:
        logger.info(f"Getting workflow job by job_id={job_id}, enterprise={enterpriseName}")
        
        job = find_reddit_workflow_job_by_job_id(job_id, enterprise_name=enterpriseName)
        
        if not job:
            raise HTTPException(status_code=404, detail=f"Workflow job not found: {job_id}")
        
        return {
            "status": "success",
            "job": job.to_dict()
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting workflow job: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/v1/workflow-jobs/update/{job_id}")
async def update_workflow_job(
    job_id: str,
    request: UpdateWorkflowJobRequest,
    enterpriseName: Optional[str] = Query(None, description="Enterprise name for database routing")
):
    """Update a Reddit workflow job's progress."""
    try:
        logger.info(f"Updating workflow job: job_id={job_id}, status={request.status}, step={request.current_step}, progress={request.progress_percentage}%")
        
        job = update_reddit_workflow_job_progress(
            job_id=job_id,
            status=request.status,
            current_step=request.current_step,
            progress_percentage=request.progress_percentage,
            step_details=request.step_details,
            error_message=request.error_message,
            completed=request.completed,
            enterprise_name=enterpriseName
        )
        
        if not job:
            raise HTTPException(status_code=404, detail=f"Workflow job not found: {job_id}")
        
        return {
            "status": "success",
            "message": "Workflow job updated",
            "job": job.to_dict()
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating workflow job: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


