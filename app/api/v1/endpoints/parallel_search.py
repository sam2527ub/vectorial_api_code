"""Parallel AI search endpoints with async trigger/polling pattern."""
from typing import Optional
from fastapi import APIRouter, HTTPException, Path, Query
from app.models.schemas import ParallelSearchRequest
from app.config import logger
from app.services.web_indexing_service import request_handler as web_indexing_handler

router = APIRouter()


@router.post("/api/search/parallel/async")
async def trigger_async_parallel_search(payload: ParallelSearchRequest, enterpriseName: Optional[str] = Query(None)):
    """
    Trigger an async parallel search job (non-blocking).

    Creates a job record and starts the Parallel AI FindAll run without waiting for completion.
    Returns job_id immediately for polling status.
    """
    logger.info(f"=== TRIGGER ASYNC PARALLEL SEARCH REQUEST START ===")
    logger.info(f"Request: query={payload.query}, model={payload.model}, match_limit={payload.match_limit}, enterprise={enterpriseName}")

    try:
        response = await web_indexing_handler.start_async_job(
            query=payload.query,
            model=payload.model,
            match_limit=payload.match_limit,
            enterprise_name=enterpriseName,
        )
        logger.info(f"=== TRIGGER ASYNC PARALLEL SEARCH REQUEST SUCCESS ===")
        return response
    except HTTPException:
        raise
    except Exception as e:
        error_message = str(e)
        error_type = type(e).__name__
        logger.error(f"Error in async parallel search trigger [{error_type}]: {error_message}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "Failed to trigger parallel search job",
                "message": error_message,
                "error_type": error_type,
            },
        )


@router.get("/api/search/parallel/status/{job_id}")
async def get_parallel_search_status(
    job_id: str = Path(..., description="Job ID returned from /api/search/parallel/async"),
    enterpriseName: Optional[str] = Query(None, description="Enterprise name (gamma, app, entelligence, beta). If not provided, uses default audience database."),
):
    """
    Check the status of an async parallel search job.
    If job is PENDING or PROCESSING, checks Parallel API for completion.
    If completed, fetches LinkedIn URLs from Parallel (NO Apify enrichment here).

    Apify enrichment is handled separately in a different step.

    Query Parameters:
    - enterpriseName (optional): Enterprise name to determine which database to use
    """
    logger.info(f"=== GET PARALLEL SEARCH STATUS REQUEST START ===")
    logger.info(f"Request: job_id={job_id}, enterprise={enterpriseName}")

    try:
        return await web_indexing_handler.get_job_status(job_id, enterprise_name=enterpriseName)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting job status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

