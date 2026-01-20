"""Async Profile Summaries endpoints for long-running summary generation jobs."""
import asyncio
import uuid
from datetime import datetime
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, HTTPException, Path, Query, BackgroundTasks
from pydantic import BaseModel
from app.config import s3_client, s3_bucket, logger
from app.utils.helpers import ensure_db_available
from app.services.summary_service import process_profile_summary
from app import database

router = APIRouter()

# Configuration
CHUNK_SIZE = 10  # Profiles per chunk
MAX_CONCURRENT = 2  # Concurrent OpenAI calls per chunk


class AsyncSummariesRequest(BaseModel):
    """Request model for async summaries trigger."""
    audienceRoomId: str
    enterpriseName: Optional[str] = None
    chunkSize: int = CHUNK_SIZE


class SummariesJobStatus(BaseModel):
    """Status model for summaries job."""
    job_id: str
    status: str  # PENDING, PROCESSING, COMPLETED, FAILED
    audience_room_id: str
    total_profiles: int = 0
    processed_profiles: int = 0
    success_count: int = 0
    skipped_count: int = 0
    error_count: int = 0
    current_chunk: int = 0
    total_chunks: int = 0
    error: Optional[str] = None
    created_at: str
    updated_at: str


# In-memory job storage (in production, use database or Redis)
_summaries_jobs: Dict[str, Dict[str, Any]] = {}


async def process_summaries_job(
    job_id: str,
    audience_room_id: str,
    enterprise_name: Optional[str],
    chunk_size: int
):
    """
    Background task to process profile summaries in chunks.
    This runs asynchronously and updates job status as it progresses.
    """
    global _summaries_jobs
    
    try:
        logger.info(f"[SummariesJob {job_id}] Starting profile summaries processing")
        
        # Fetch audience room and profiles
        audience_room = database.find_audience_room_by_id(
            audience_room_id, 
            include_profiles=True, 
            enterprise_name=enterprise_name
        )
        
        if not audience_room:
            _summaries_jobs[job_id]["status"] = "FAILED"
            _summaries_jobs[job_id]["error"] = f"Audience room {audience_room_id} not found"
            _summaries_jobs[job_id]["updated_at"] = datetime.utcnow().isoformat()
            return
        
        all_profiles = audience_room.profiles or []
        total_profiles = len(all_profiles)
        total_chunks = (total_profiles + chunk_size - 1) // chunk_size
        
        if total_profiles == 0:
            _summaries_jobs[job_id]["status"] = "COMPLETED"
            _summaries_jobs[job_id]["total_profiles"] = 0
            _summaries_jobs[job_id]["updated_at"] = datetime.utcnow().isoformat()
            return
        
        _summaries_jobs[job_id]["total_profiles"] = total_profiles
        _summaries_jobs[job_id]["total_chunks"] = total_chunks
        _summaries_jobs[job_id]["status"] = "PROCESSING"
        _summaries_jobs[job_id]["updated_at"] = datetime.utcnow().isoformat()
        
        logger.info(f"[SummariesJob {job_id}] Processing {total_profiles} profiles in {total_chunks} chunks")
        
        # Process profiles in chunks
        total_success = 0
        total_skipped = 0
        total_errors = 0
        processed = 0
        
        for chunk_num in range(total_chunks):
            chunk_start = chunk_num * chunk_size
            chunk_end = min(chunk_start + chunk_size, total_profiles)
            profiles_chunk = all_profiles[chunk_start:chunk_end]
            
            logger.info(f"[SummariesJob {job_id}] Processing chunk {chunk_num + 1}/{total_chunks} ({len(profiles_chunk)} profiles)")
            
            # Update current chunk
            _summaries_jobs[job_id]["current_chunk"] = chunk_num + 1
            _summaries_jobs[job_id]["updated_at"] = datetime.utcnow().isoformat()
            
            # Process chunk with rate limiting
            semaphore = asyncio.Semaphore(MAX_CONCURRENT)
            
            async def rate_limited_process(profile):
                async with semaphore:
                    result = await process_profile_summary(profile, audience_room_id, enterprise_name=enterprise_name)
                    await asyncio.sleep(1.0)  # Rate limiting
                    return result
            
            tasks = [rate_limited_process(profile) for profile in profiles_chunk]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Process results
            for idx, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(f"[SummariesJob {job_id}] Error processing profile {profiles_chunk[idx].id}: {result}")
                    total_errors += 1
                elif result["status"] == "success":
                    total_success += 1
                elif result["status"] == "skipped":
                    total_skipped += 1
                else:
                    total_errors += 1
                
                processed += 1
            
            # Update progress after each chunk
            _summaries_jobs[job_id]["processed_profiles"] = processed
            _summaries_jobs[job_id]["success_count"] = total_success
            _summaries_jobs[job_id]["skipped_count"] = total_skipped
            _summaries_jobs[job_id]["error_count"] = total_errors
            _summaries_jobs[job_id]["updated_at"] = datetime.utcnow().isoformat()
            
            logger.info(f"[SummariesJob {job_id}] Chunk {chunk_num + 1} complete: {total_success} success, {total_skipped} skipped, {total_errors} errors")
            
            # Small delay between chunks
            await asyncio.sleep(0.5)
        
        # Mark job as completed
        _summaries_jobs[job_id]["status"] = "COMPLETED"
        _summaries_jobs[job_id]["updated_at"] = datetime.utcnow().isoformat()
        
        logger.info(f"[SummariesJob {job_id}] Completed: {total_success} success, {total_skipped} skipped, {total_errors} errors")
        
    except Exception as e:
        logger.error(f"[SummariesJob {job_id}] Failed: {str(e)}", exc_info=True)
        _summaries_jobs[job_id]["status"] = "FAILED"
        _summaries_jobs[job_id]["error"] = str(e)
        _summaries_jobs[job_id]["updated_at"] = datetime.utcnow().isoformat()


@router.post("/api/v1/audience-rooms/{audience_room_id}/generate-summaries/async")
async def trigger_async_summaries(
    audience_room_id: str = Path(...),
    enterpriseName: Optional[str] = Query(None),
    chunkSize: int = Query(CHUNK_SIZE, ge=1, le=20),
    background_tasks: BackgroundTasks = None
):
    """
    Trigger async profile summaries generation that runs in the background.
    Returns immediately with a job_id for polling status.
    
    This endpoint is designed for large audience rooms (100+ profiles)
    that would otherwise timeout with the synchronous endpoint.
    
    Path Parameters:
    - audience_room_id: UUID of the audience room
    
    Query Parameters:
    - enterpriseName (optional): Enterprise name for database selection
    - chunkSize (optional): Profiles per chunk (default: 10, max: 20)
    """
    logger.info(f"=== ASYNC SUMMARIES TRIGGER START ===")
    logger.info(f"Request: room_id={audience_room_id}, enterprise={enterpriseName}, chunkSize={chunkSize}")
    
    ensure_db_available("audience")
    
    from app.config import openai_client
    if not openai_client:
        raise HTTPException(status_code=503, detail="OpenAI client not initialized. Please set OPENAI_API_KEY.")
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured.")
    
    # Verify audience room exists
    audience_room = database.find_audience_room_by_id(audience_room_id, include_profiles=False, enterprise_name=enterpriseName)
    if not audience_room:
        raise HTTPException(status_code=404, detail=f"Audience room {audience_room_id} not found")
    
    # Generate job ID
    job_id = str(uuid.uuid4())
    
    # Create job record
    _summaries_jobs[job_id] = {
        "job_id": job_id,
        "status": "PENDING",
        "audience_room_id": audience_room_id,
        "enterprise_name": enterpriseName,
        "total_profiles": 0,
        "processed_profiles": 0,
        "success_count": 0,
        "skipped_count": 0,
        "error_count": 0,
        "current_chunk": 0,
        "total_chunks": 0,
        "error": None,
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat()
    }
    
    # Add background task
    background_tasks.add_task(
        process_summaries_job,
        job_id=job_id,
        audience_room_id=audience_room_id,
        enterprise_name=enterpriseName,
        chunk_size=chunkSize
    )
    
    logger.info(f"=== ASYNC SUMMARIES TRIGGERED: job_id={job_id} ===")
    
    return {
        "job_id": job_id,
        "status": "PENDING",
        "audience_room_id": audience_room_id,
        "message": "Profile summaries job started. Use /generate-summaries/async/status/{job_id} to check progress."
    }


@router.get("/api/v1/audience-rooms/{audience_room_id}/generate-summaries/async/status/{job_id}")
async def get_async_summaries_status(
    audience_room_id: str = Path(...),
    job_id: str = Path(..., description="Job ID returned from trigger endpoint"),
    enterpriseName: Optional[str] = Query(None)
):
    """
    Check the status of an async profile summaries job.
    
    Returns current progress including:
    - status: PENDING, PROCESSING, COMPLETED, FAILED
    - total_profiles: Total profiles to process
    - processed_profiles: Number of profiles processed so far
    - current_chunk / total_chunks: Chunk progress
    - success_count, skipped_count, error_count: Result counts
    """
    logger.info(f"Checking async summaries status for job_id={job_id}")
    
    if job_id not in _summaries_jobs:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    
    job = _summaries_jobs[job_id]
    
    # Verify audience room matches
    if job["audience_room_id"] != audience_room_id:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found for audience room {audience_room_id}")
    
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "audience_room_id": job["audience_room_id"],
        "total_profiles": job["total_profiles"],
        "processed_profiles": job["processed_profiles"],
        "success_count": job["success_count"],
        "skipped_count": job["skipped_count"],
        "error_count": job["error_count"],
        "current_chunk": job["current_chunk"],
        "total_chunks": job["total_chunks"],
        "error": job["error"],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"]
    }
