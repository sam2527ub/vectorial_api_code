"""Web indexing endpoints for parallel search."""
from typing import Optional
from fastapi import APIRouter, HTTPException, Path, Query
from app.config import logger
from app.services.web_indexing_service import request_handler
from app.services.web_indexing_service.schemas import ParallelScrapeRequest, ParallelScrapeStatusResponse

router = APIRouter()


@router.post("/api/search/parallel/async")
async def trigger_parallel_search_async(
    payload: ParallelScrapeRequest,
    enterpriseName: Optional[str] = Query(None, description="Enterprise name for database routing")
) -> dict:
    """Trigger Async Parallel Search."""
    logger.info("=" * 80)
    logger.info("REQUEST: POST /api/search/parallel/async")
    logger.info(f"  Query: {payload.query}")
    logger.info(f"  Model: {payload.model}")
    logger.info(f"  Match Limit: {payload.match_limit}")
    logger.info(f"  Entity Type: {payload.entity_type}")
    logger.info(f"  Enterprise: {enterpriseName or 'default'}")
    logger.info("=" * 80)
    
    try:
        response = await request_handler.start_scraping_job(
            query=payload.query,
            model=payload.model,
            match_limit=payload.match_limit,
            entity_type=payload.entity_type,
            enterprise_name=enterpriseName
        )
        
        logger.info("RESPONSE: POST /api/search/parallel/async")
        logger.info(f"  Job ID: {response.get('job_id', 'unknown')}")
        logger.info(f"  Status: {response.get('status', 'unknown')}")
        logger.info("=" * 80)
        
        return response
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in trigger_parallel_search_async: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to start scraping: {str(e)}")


@router.get("/api/search/parallel/status/{job_id}")
async def get_parallel_scrape_status(
    job_id: str = Path(..., description="Job ID returned from start endpoint"),
    enterpriseName: Optional[str] = Query(None, description="Enterprise name for database routing")
) -> dict:
    """Get status of an async Parallel search job."""
    from datetime import datetime
    request_start_time = datetime.now()
    logger.info(f"[STATUS] GET /api/search/parallel/status/{job_id}, enterprise={enterpriseName or 'default'}")
    
    try:
        response = await request_handler.get_job_status(job_id, enterpriseName)
        
        total_elapsed = (datetime.now() - request_start_time).total_seconds()
        logger.info(f"[STATUS] Response sent in {total_elapsed:.2f}s, status={response.get('status', 'unknown')}")
        
        return response
    
    except HTTPException:
        raise
    except Exception as e:
        total_elapsed = (datetime.now() - request_start_time).total_seconds()
        logger.error(f"[STATUS] Error in get_parallel_scrape_status after {total_elapsed:.2f}s: {e}", exc_info=True)
        return {
            "job_id": job_id,
            "status": "processing",
            "message": f"Error checking status, will retry: {str(e)}",
            "subreddits_collected": 0,
            "subreddits": []
        }
