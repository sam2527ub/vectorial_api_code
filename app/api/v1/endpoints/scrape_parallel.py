"""Parallel/Batched Scraping endpoints - uses User Observation Fetch service."""
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel

from app.models.schemas import Cookie
from app.config import logger
from app.services.user_observation_fetch_service import request_handler as user_observation_fetch_handler

router = APIRouter()

# Re-export for API compatibility
MAX_URLS_PER_BATCH = 30


class ParallelScrapeRequest(BaseModel):
    """Request model for parallel scraping (POST /api/v1/scrape/parallel)."""
    linkedin_urls: List[str]
    cookies: List[Cookie]
    user_agent: str
    audience_room_id: Optional[str] = None
    max_posts: int = 25
    enterpriseName: Optional[str] = None
    batch_size: int = MAX_URLS_PER_BATCH


class BatchStatus(BaseModel):
    """Status of a single batch."""
    batch_id: str
    apify_run_id: Optional[str]
    status: str
    urls_count: int
    error: Optional[str] = None


class ParallelScrapeStatus(BaseModel):
    """Overall parallel scrape job status."""
    job_id: str
    status: str
    total_urls: int
    total_batches: int
    completed_batches: int
    failed_batches: int
    batches: List[BatchStatus]
    created_at: str
    updated_at: str


@router.post("/api/v1/scrape/parallel")
async def trigger_parallel_scraping(payload: ParallelScrapeRequest):
    """
    Triggers parallel/batched Apify scraping for faster processing.
    
    Instead of one Apify run with all URLs, this:
    1. Splits URLs into batches (default 30 URLs per batch)
    2. Starts multiple Apify runs in parallel (max 3 concurrent)
    3. Returns immediately with job_id for polling
    
    This can reduce scraping time from 22 minutes (82 profiles) to ~8 minutes.
    
    Request Body:
    - linkedin_urls: List of LinkedIn profile URLs to scrape
    - cookies: LinkedIn authentication cookies
    - user_agent: Browser user agent string
    - audience_room_id (optional): Audience room to update
    - max_posts: Maximum posts per profile (default: 25)
    - enterpriseName (optional): Enterprise name for database
    - batch_size (optional): URLs per batch (default: 30)
    """
    logger.info("=== PARALLEL SCRAPE REQUEST START ===")
    logger.info(f"Request: enterprise={payload.enterpriseName}, urls_count={len(payload.linkedin_urls)}, batch_size={payload.batch_size}")

    try:
        return await user_observation_fetch_handler.trigger_parallel_scraping(
            linkedin_urls=payload.linkedin_urls,
            cookies=payload.cookies,
            user_agent=payload.user_agent,
            max_posts=payload.max_posts,
            audience_room_id=payload.audience_room_id,
            enterprise_name=payload.enterpriseName,
            batch_size=payload.batch_size,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in trigger_parallel_scraping: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/v1/scrape/parallel/status/{job_id}")
async def get_parallel_scrape_status(
    job_id: str = Path(..., description="Job ID from parallel scrape trigger"),
    enterpriseName: Optional[str] = Query(None, description="Enterprise name"),
):
    """
    Check status of a parallel scraping job.

    Reads from database (ScrapeJob table). Polls batch Apify runs and aggregates results.
    When all batches complete, processes posts and updates profiles.
    """
    logger.info(f"Checking parallel scrape status for job_id={job_id}, enterprise={enterpriseName}")

    try:
        return await user_observation_fetch_handler.get_job_status(job_id, enterprise_name=enterpriseName)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in get_parallel_scrape_status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
