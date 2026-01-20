"""Parallel/Batched Scraping endpoints for faster processing."""
import logging
import asyncio
import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Path, Query, BackgroundTasks
from pydantic import BaseModel
from app.models.schemas import ScrapeRequest, Cookie
from app.config import apify_client, POST_SCRAPER_ACTOR_ID, logger
from app.utils.helpers import normalize_linkedin_url, ensure_db_available
from app.services.profile_service import process_posts_and_update_profiles
from app import database

router = APIRouter()

# Configuration
MAX_URLS_PER_BATCH = 30  # Optimal batch size for Apify
MAX_CONCURRENT_BATCHES = 3  # Max parallel Apify runs


class ParallelScrapeRequest(BaseModel):
    """Request model for parallel scraping."""
    linkedin_urls: List[str]
    cookies: List[Cookie]
    user_agent: str
    audience_room_id: Optional[str] = None
    max_posts: int = 25
    enterpriseName: Optional[str] = None
    batch_size: int = MAX_URLS_PER_BATCH  # Override batch size if needed


class BatchStatus(BaseModel):
    """Status of a single batch."""
    batch_id: str
    apify_run_id: Optional[str]
    status: str  # PENDING, RUNNING, SUCCEEDED, FAILED
    urls_count: int
    error: Optional[str] = None


class ParallelScrapeStatus(BaseModel):
    """Overall parallel scrape job status."""
    job_id: str
    status: str  # PENDING, PROCESSING, COMPLETED, FAILED
    total_urls: int
    total_batches: int
    completed_batches: int
    failed_batches: int
    batches: List[BatchStatus]
    created_at: str
    updated_at: str


# In-memory storage for parallel scrape jobs (in production, use database)
_parallel_scrape_jobs: Dict[str, Dict[str, Any]] = {}


def split_into_batches(urls: List[str], batch_size: int) -> List[List[str]]:
    """Split URLs into batches of specified size."""
    return [urls[i:i + batch_size] for i in range(0, len(urls), batch_size)]


async def process_batch(
    batch_id: str,
    urls: List[str],
    cookies_dict: List[dict],
    user_agent: str,
    max_posts: int
) -> Dict[str, Any]:
    """
    Process a single batch of URLs with Apify.
    Returns batch result with run_id and status.
    """
    apify_urls = []
    for url in urls:
        url = url.strip()
        if not url.startswith("http://") and not url.startswith("https://"):
            url = "https://" + url
        if url.startswith("https://linkedin.com") and not url.startswith("https://www.linkedin.com"):
            url = url.replace("https://linkedin.com", "https://www.linkedin.com")
        elif url.startswith("http://linkedin.com") and not url.startswith("http://www.linkedin.com"):
            url = url.replace("http://linkedin.com", "https://www.linkedin.com")
        apify_urls.append(url)
    
    run_input = {
        "urls": apify_urls,
        "limitPerSource": max_posts,
        "cookie": cookies_dict,
        "userAgent": user_agent,
        "proxy": {"useApifyProxy": True}
    }
    
    try:
        # Start Apify actor
        run = apify_client.actor(POST_SCRAPER_ACTOR_ID).start(run_input=run_input)
        run_data = run.get("data", run) if isinstance(run, dict) else {}
        apify_run_id = run_data.get('id')
        
        if not apify_run_id:
            raise Exception("Apify start did not return a run id")
        
        return {
            "batch_id": batch_id,
            "apify_run_id": apify_run_id,
            "status": "RUNNING",
            "urls_count": len(urls),
            "error": None
        }
    except Exception as e:
        return {
            "batch_id": batch_id,
            "apify_run_id": None,
            "status": "FAILED",
            "urls_count": len(urls),
            "error": str(e)
        }


async def start_batches_parallel(
    job_id: str,
    batches: List[List[str]],
    cookies_dict: List[dict],
    user_agent: str,
    max_posts: int
) -> List[Dict[str, Any]]:
    """Start multiple batches in parallel with concurrency limit."""
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_BATCHES)
    
    async def process_with_semaphore(batch_id: str, urls: List[str]):
        async with semaphore:
            return await process_batch(batch_id, urls, cookies_dict, user_agent, max_posts)
    
    tasks = []
    for i, batch_urls in enumerate(batches):
        batch_id = f"{job_id}_batch_{i}"
        tasks.append(process_with_semaphore(batch_id, batch_urls))
    
    return await asyncio.gather(*tasks)


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
    logger.info(f"=== PARALLEL SCRAPE REQUEST START ===")
    logger.info(f"Request: enterprise={payload.enterpriseName}, urls_count={len(payload.linkedin_urls)}, batch_size={payload.batch_size}")
    
    ensure_db_available("audience")
    
    if not apify_client:
        raise HTTPException(status_code=503, detail="Apify client not initialized. Please set APIFY_API_TOKEN.")
    
    # Convert cookies to dict
    cookies_dict = [cookie.dict(exclude_none=True) for cookie in payload.cookies]
    
    # Normalize URLs
    normalized_urls = [u for u in (normalize_linkedin_url(u) for u in payload.linkedin_urls) if u]
    
    # Split into batches
    batch_size = min(payload.batch_size, MAX_URLS_PER_BATCH)
    batches = split_into_batches(payload.linkedin_urls, batch_size)
    
    logger.info(f"Split {len(payload.linkedin_urls)} URLs into {len(batches)} batches of ~{batch_size} URLs each")
    
    # Generate job ID
    job_id = str(uuid.uuid4())
    
    # Create job record in database
    try:
        job = database.create_scrape_job(
            linkedin_urls=normalized_urls,
            max_posts=payload.max_posts,
            audience_room_id=payload.audience_room_id,
            enterprise_name=payload.enterpriseName,
        )
        job_id = job.id
        logger.info(f"Created scrape job {job_id} for parallel scraping")
    except Exception as e:
        logger.warning(f"Failed to create scrape job in database: {e}")
    
    # Start all batches in parallel
    batch_results = await start_batches_parallel(
        job_id=job_id,
        batches=batches,
        cookies_dict=cookies_dict,
        user_agent=payload.user_agent,
        max_posts=payload.max_posts
    )
    
    # Create job status record
    _parallel_scrape_jobs[job_id] = {
        "job_id": job_id,
        "status": "PROCESSING",
        "total_urls": len(payload.linkedin_urls),
        "total_batches": len(batches),
        "completed_batches": 0,
        "failed_batches": sum(1 for b in batch_results if b["status"] == "FAILED"),
        "batches": batch_results,
        "audience_room_id": payload.audience_room_id,
        "enterprise_name": payload.enterpriseName,
        "linkedin_urls": normalized_urls,
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat()
    }
    
    # Update database job with batch info
    try:
        apify_run_ids = [b["apify_run_id"] for b in batch_results if b["apify_run_id"]]
        database.update_scrape_job(job_id, {
            "status": "PROCESSING",
            "apifyRunId": apify_run_ids[0] if apify_run_ids else None,  # Store first run ID
            "result": {
                "parallel_mode": True,
                "total_batches": len(batches),
                "batch_run_ids": apify_run_ids
            }
        }, enterprise_name=payload.enterpriseName)
    except Exception as e:
        logger.warning(f"Failed to update scrape job: {e}")
    
    running_count = sum(1 for b in batch_results if b["status"] == "RUNNING")
    failed_count = sum(1 for b in batch_results if b["status"] == "FAILED")
    
    logger.info(f"=== PARALLEL SCRAPE STARTED: {running_count} batches running, {failed_count} failed ===")
    
    return {
        "job_id": job_id,
        "status": "PROCESSING",
        "total_urls": len(payload.linkedin_urls),
        "total_batches": len(batches),
        "batches_started": running_count,
        "batches_failed": failed_count,
        "message": f"Parallel scraping started with {len(batches)} batches. Use /api/v1/scrape/parallel/status/{job_id} to check progress."
    }


@router.get("/api/v1/scrape/parallel/status/{job_id}")
async def get_parallel_scrape_status(
    job_id: str = Path(..., description="Job ID from parallel scrape trigger"),
    enterpriseName: Optional[str] = Query(None, description="Enterprise name")
):
    """
    Check status of a parallel scraping job.
    
    Polls all batch Apify runs and aggregates results.
    When all batches complete, processes posts and updates profiles.
    """
    logger.info(f"Checking parallel scrape status for job_id={job_id}")
    
    if job_id not in _parallel_scrape_jobs:
        # Check if it's a regular scrape job
        raise HTTPException(status_code=404, detail=f"Parallel scrape job {job_id} not found")
    
    job = _parallel_scrape_jobs[job_id]
    
    # If already completed or failed, return cached result
    if job["status"] in ["COMPLETED", "FAILED"]:
        return {
            "job_id": job["job_id"],
            "status": job["status"],
            "total_urls": job["total_urls"],
            "total_batches": job["total_batches"],
            "completed_batches": job["completed_batches"],
            "failed_batches": job["failed_batches"],
            "result": job.get("result"),
            "created_at": job["created_at"],
            "updated_at": job["updated_at"]
        }
    
    # Poll each batch status
    completed = 0
    failed = 0
    still_running = 0
    all_dataset_ids = []
    
    for batch in job["batches"]:
        if batch["status"] in ["SUCCEEDED", "COMPLETED"]:
            completed += 1
            if batch.get("dataset_id"):
                all_dataset_ids.append(batch["dataset_id"])
        elif batch["status"] == "FAILED":
            failed += 1
        elif batch["apify_run_id"]:
            # Check Apify status
            try:
                run = apify_client.run(batch["apify_run_id"]).get()
                run_data = run.get("data", run) if isinstance(run, dict) else {}
                run_status = run_data.get("status") if isinstance(run_data, dict) else None
                
                if run_status == "SUCCEEDED":
                    batch["status"] = "SUCCEEDED"
                    batch["dataset_id"] = run_data.get("defaultDatasetId")
                    completed += 1
                    if batch["dataset_id"]:
                        all_dataset_ids.append(batch["dataset_id"])
                elif run_status == "FAILED":
                    batch["status"] = "FAILED"
                    batch["error"] = run_data.get("statusMessage", "Unknown error")
                    failed += 1
                else:
                    still_running += 1
            except Exception as e:
                logger.warning(f"Error checking batch {batch['batch_id']}: {e}")
                still_running += 1
    
    job["completed_batches"] = completed
    job["failed_batches"] = failed
    job["updated_at"] = datetime.utcnow().isoformat()
    
    # Check if all batches are done
    if still_running == 0:
        # All batches completed
        if failed == len(job["batches"]):
            job["status"] = "FAILED"
            job["result"] = {"error": "All batches failed"}
        else:
            # Process posts from all successful batches
            total_posts = 0
            total_profiles_updated = 0
            
            for dataset_id in all_dataset_ids:
                try:
                    dataset_client = apify_client.dataset(dataset_id)
                    
                    processing_result = await process_posts_and_update_profiles(
                        dataset_client=dataset_client,
                        job_id=job_id,
                        audience_room_id=job.get("audience_room_id"),
                        linkedin_urls=job.get("linkedin_urls"),
                        enterprise_name=job.get("enterprise_name"),
                    )
                    
                    total_posts += processing_result.get("posts_found", 0)
                    total_profiles_updated += processing_result.get("profiles_updated", 0)
                except Exception as e:
                    logger.error(f"Error processing dataset {dataset_id}: {e}")
            
            job["status"] = "COMPLETED"
            job["result"] = {
                "posts_found": total_posts,
                "profiles_updated": total_profiles_updated,
                "datasets_processed": len(all_dataset_ids)
            }
            
            # Update database
            try:
                database.update_scrape_job(job_id, {
                    "status": "COMPLETED",
                    "result": job["result"]
                }, enterprise_name=job.get("enterprise_name"))
            except Exception as e:
                logger.warning(f"Failed to update job status in database: {e}")
    
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "total_urls": job["total_urls"],
        "total_batches": job["total_batches"],
        "completed_batches": completed,
        "failed_batches": failed,
        "still_running": still_running,
        "result": job.get("result"),
        "created_at": job["created_at"],
        "updated_at": job["updated_at"]
    }
