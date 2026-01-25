"""Async Profile Summaries endpoints for long-running summary generation jobs.

Uses database (SummariesJob table) for job tracking instead of in-memory storage.
This ensures job state persists across Vercel cold starts.

Uses self-triggering chunk processing to avoid Vercel 13-minute timeout:
- Processes 3 chunks per API call (30 profiles)
- After processing, triggers next batch via HTTP call
- Each API call completes within timeout
- Scales to 1000+ profiles
"""
import asyncio
import uuid
import os
import httpx
from datetime import datetime
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, HTTPException, Path, Query, BackgroundTasks, Request
from pydantic import BaseModel
from app.config import s3_client, s3_bucket, logger
from app.utils.helpers import ensure_db_available
from app.services.summary_service import process_profile_summary
from app import database
from app.database.connection import get_enterprise_audience_connection

router = APIRouter()

# Configuration
CHUNK_SIZE = 10  # Profiles per chunk
CHUNKS_PER_API_CALL = 3  # Process 3 chunks per API call (30 profiles)
MAX_CONCURRENT = 2  # Concurrent OpenAI calls per chunk


class AsyncSummariesRequest(BaseModel):
    """Request model for async summaries trigger."""
    audienceRoomId: str
    enterpriseName: Optional[str] = None
    chunkSize: int = CHUNK_SIZE
    taskToken: Optional[str] = None  # Step Functions callback token


# Database operations for SummariesJob

def ensure_summaries_job_table_exists(enterprise_name: Optional[str] = None) -> None:
    """Create SummariesJob table if it doesn't exist. Safe - won't affect existing data."""
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS "SummariesJob" (
                    "id" TEXT NOT NULL,
                    "status" TEXT NOT NULL DEFAULT 'PENDING',
                    "audienceRoomId" TEXT NOT NULL,
                    "totalProfiles" INTEGER NOT NULL DEFAULT 0,
                    "processedProfiles" INTEGER NOT NULL DEFAULT 0,
                    "successCount" INTEGER NOT NULL DEFAULT 0,
                    "skippedCount" INTEGER NOT NULL DEFAULT 0,
                    "errorCount" INTEGER NOT NULL DEFAULT 0,
                    "currentChunk" INTEGER NOT NULL DEFAULT 0,
                    "totalChunks" INTEGER NOT NULL DEFAULT 0,
                    "error" TEXT,
                    "taskToken" TEXT,
                    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    "updatedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT "SummariesJob_pkey" PRIMARY KEY ("id")
                )
            """)
        with conn.cursor() as cur:
            cur.execute('CREATE INDEX IF NOT EXISTS "SummariesJob_status_idx" ON "SummariesJob"("status")')
        with conn.cursor() as cur:
            cur.execute('CREATE INDEX IF NOT EXISTS "SummariesJob_audienceRoomId_idx" ON "SummariesJob"("audienceRoomId")')
    logger.info("SummariesJob table ensured to exist")


def create_summaries_job(
    job_id: str,
    audience_room_id: str,
    task_token: Optional[str] = None,
    enterprise_name: Optional[str] = None
) -> Dict[str, Any]:
    """Create a new summaries job in the database."""
    # Auto-create table if it doesn't exist
    ensure_summaries_job_table_exists(enterprise_name)
    
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO "SummariesJob" 
                (id, status, "audienceRoomId", "taskToken", "createdAt", "updatedAt")
                VALUES (%s, %s, %s, %s, NOW(), NOW())
                RETURNING id, status, "audienceRoomId", "totalProfiles", 
                          "processedProfiles", "successCount", "skippedCount", "errorCount",
                          "currentChunk", "totalChunks", error, "taskToken",
                          "createdAt", "updatedAt"
            """, (job_id, 'PENDING', audience_room_id, task_token))
            row = cur.fetchone()
            return {
                "job_id": row[0],
                "status": row[1],
                "audience_room_id": row[2],
                "total_profiles": row[3],
                "processed_profiles": row[4],
                "success_count": row[5],
                "skipped_count": row[6],
                "error_count": row[7],
                "current_chunk": row[8],
                "total_chunks": row[9],
                "error": row[10],
                "task_token": row[11],
                "created_at": row[12].isoformat() if row[12] else None,
                "updated_at": row[13].isoformat() if row[13] else None
            }


def get_summaries_job(job_id: str, enterprise_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Get a summaries job from the database."""
    # Auto-create table if it doesn't exist
    ensure_summaries_job_table_exists(enterprise_name)
    
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, status, "audienceRoomId", "totalProfiles", 
                       "processedProfiles", "successCount", "skippedCount", "errorCount",
                       "currentChunk", "totalChunks", error, "taskToken",
                       "createdAt", "updatedAt"
                FROM "SummariesJob"
                WHERE id = %s
            """, (job_id,))
            row = cur.fetchone()
            if not row:
                return None
            return {
                "job_id": row[0],
                "status": row[1],
                "audience_room_id": row[2],
                "total_profiles": row[3],
                "processed_profiles": row[4],
                "success_count": row[5],
                "skipped_count": row[6],
                "error_count": row[7],
                "current_chunk": row[8],
                "total_chunks": row[9],
                "error": row[10],
                "task_token": row[11],
                "created_at": row[12].isoformat() if row[12] else None,
                "updated_at": row[13].isoformat() if row[13] else None
            }


def update_summaries_job(
    job_id: str,
    enterprise_name: Optional[str] = None,
    **kwargs
) -> None:
    """Update a summaries job in the database."""
    # Auto-create table if it doesn't exist
    ensure_summaries_job_table_exists(enterprise_name)
    
    # Build dynamic update query
    set_clauses = ['"updatedAt" = NOW()']
    values = []
    
    field_mapping = {
        'status': 'status',
        'total_profiles': '"totalProfiles"',
        'processed_profiles': '"processedProfiles"',
        'success_count': '"successCount"',
        'skipped_count': '"skippedCount"',
        'error_count': '"errorCount"',
        'current_chunk': '"currentChunk"',
        'total_chunks': '"totalChunks"',
        'error': 'error'
    }
    
    for key, db_field in field_mapping.items():
        if key in kwargs:
            set_clauses.append(f"{db_field} = %s")
            values.append(kwargs[key])
    
    values.append(job_id)
    
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                UPDATE "SummariesJob"
                SET {', '.join(set_clauses)}
                WHERE id = %s
            """, values)


def get_pending_summaries_jobs(enterprise_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """Get all pending/processing summaries jobs."""
    # Auto-create table if it doesn't exist
    ensure_summaries_job_table_exists(enterprise_name)
    
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, status, "audienceRoomId", "totalProfiles", 
                       "processedProfiles", "successCount", "skippedCount", "errorCount",
                       "currentChunk", "totalChunks", error, "taskToken",
                       "createdAt", "updatedAt"
                FROM "SummariesJob"
                WHERE status IN ('PENDING', 'PROCESSING')
                ORDER BY "createdAt" DESC
            """)
            rows = cur.fetchall()
            return [{
                "job_id": row[0],
                "status": row[1],
                "audience_room_id": row[2],
                "total_profiles": row[3],
                "processed_profiles": row[4],
                "success_count": row[5],
                "skipped_count": row[6],
                "error_count": row[7],
                "current_chunk": row[8],
                "total_chunks": row[9],
                "error": row[10],
                "task_token": row[11],
                "created_at": row[12].isoformat() if row[12] else None,
                "updated_at": row[13].isoformat() if row[13] else None
            } for row in rows]


def get_base_url() -> str:
    """Get the base URL for self-triggering API calls."""
    # Try VERCEL_URL first (automatically set by Vercel)
    vercel_url = os.getenv("VERCEL_URL")
    if vercel_url:
        return f"https://{vercel_url}"
    
    # Fallback to AUDIENCE_API_BASE_URL if set
    api_url = os.getenv("AUDIENCE_API_BASE_URL")
    if api_url:
        return api_url
    
    # Fallback to audience-workflow.vercel.app (production default)
    return "https://audience-workflow.vercel.app"


async def process_summaries_chunks(
    job_id: str,
    audience_room_id: str,
    enterprise_name: Optional[str],
    chunk_size: int,
    start_chunk: int = 0,  # Which chunk to start from
    base_url: Optional[str] = None  # Base URL for self-triggering
):
    """
    Process 3 chunks of profile summaries, then self-trigger next batch.
    This avoids Vercel 13-minute timeout by processing in smaller batches.
    
    Args:
        job_id: Job ID from SummariesJob table
        audience_room_id: Audience room ID
        enterprise_name: Enterprise name for database
        chunk_size: Profiles per chunk
        start_chunk: Which chunk number to start from (0-indexed)
        base_url: Base URL for self-triggering (defaults to VERCEL_URL or AUDIENCE_API_BASE_URL)
    """
    try:
        logger.info(f"[SummariesJob {job_id}] Processing chunks starting from chunk {start_chunk}")
        
        # Get job from database
        job = get_summaries_job(job_id, enterprise_name)
        if not job:
            logger.error(f"[SummariesJob {job_id}] Job not found in database")
            return
        
        if job["status"] not in ["PENDING", "PROCESSING"]:
            logger.info(f"[SummariesJob {job_id}] Job already completed/failed: {job['status']}")
            return
        
        # Fetch audience room and profiles (only once, first time)
        if start_chunk == 0:
            audience_room = database.find_audience_room_by_id(
                audience_room_id, 
                include_profiles=True, 
                enterprise_name=enterprise_name
            )
            
            if not audience_room:
                update_summaries_job(job_id, enterprise_name, status="FAILED", error=f"Audience room {audience_room_id} not found")
                return
            
            all_profiles = audience_room.profiles or []
            total_profiles = len(all_profiles)
            total_chunks = (total_profiles + chunk_size - 1) // chunk_size
            
            if total_profiles == 0:
                update_summaries_job(job_id, enterprise_name, status="COMPLETED", total_profiles=0)
                return
            
            update_summaries_job(
                job_id, enterprise_name,
                total_profiles=total_profiles,
                total_chunks=total_chunks,
                status="PROCESSING"
            )
            
            logger.info(f"[SummariesJob {job_id}] Processing {total_profiles} profiles in {total_chunks} chunks")
        else:
            # For subsequent chunks, get total from database
            job = get_summaries_job(job_id, enterprise_name)
            total_profiles = job["total_profiles"]
            total_chunks = job["total_chunks"]
            
            # Re-fetch profiles (needed for processing)
            audience_room = database.find_audience_room_by_id(
                audience_room_id, 
                include_profiles=True, 
                enterprise_name=enterprise_name
            )
            if not audience_room:
                update_summaries_job(job_id, enterprise_name, status="FAILED", error=f"Audience room {audience_room_id} not found")
                return
            all_profiles = audience_room.profiles or []
        
        # Process CHUNKS_PER_API_CALL chunks (e.g., 3 chunks = 30 profiles)
        total_success = job.get("success_count", 0)
        total_skipped = job.get("skipped_count", 0)
        total_errors = job.get("error_count", 0)
        processed = job.get("processed_profiles", 0)
        
        chunks_to_process = min(CHUNKS_PER_API_CALL, total_chunks - start_chunk)
        
        for chunk_offset in range(chunks_to_process):
            chunk_num = start_chunk + chunk_offset
            
            if chunk_num >= total_chunks:
                break
            
            chunk_start = chunk_num * chunk_size
            chunk_end = min(chunk_start + chunk_size, total_profiles)
            profiles_chunk = all_profiles[chunk_start:chunk_end]
            
            logger.info(f"[SummariesJob {job_id}] Processing chunk {chunk_num + 1}/{total_chunks} ({len(profiles_chunk)} profiles)")
            
            # Update current chunk in DATABASE
            update_summaries_job(job_id, enterprise_name, current_chunk=chunk_num + 1)
            
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
            
            # Update progress in DATABASE after each chunk
            update_summaries_job(
                job_id, enterprise_name,
                processed_profiles=processed,
                success_count=total_success,
                skipped_count=total_skipped,
                error_count=total_errors
            )
            
            logger.info(f"[SummariesJob {job_id}] Chunk {chunk_num + 1} complete: {total_success} success, {total_skipped} skipped, {total_errors} errors")
            
            # Small delay between chunks
            await asyncio.sleep(0.5)
        
        # Check if there are more chunks to process
        next_chunk = start_chunk + chunks_to_process
        
        if next_chunk < total_chunks:
            # Self-trigger next batch of chunks
            logger.info(f"[SummariesJob {job_id}] Completed chunks {start_chunk}-{start_chunk + chunks_to_process - 1}, triggering next batch starting at chunk {next_chunk}")
            
            # Use provided base_url or get from environment
            api_base_url = base_url or get_base_url()
            
            # Make async HTTP call to trigger next batch
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    trigger_url = f"{api_base_url}/api/v1/audience-rooms/{audience_room_id}/generate-summaries/async/process"
                    params = {
                        "jobId": job_id,
                        "startChunk": next_chunk,
                        "chunkSize": chunk_size
                    }
                    if enterprise_name:
                        params["enterpriseName"] = enterprise_name
                    
                    response = await client.post(trigger_url, params=params)
                    response.raise_for_status()
                    logger.info(f"[SummariesJob {job_id}] Successfully triggered next batch (chunk {next_chunk})")
            except Exception as trigger_error:
                logger.error(f"[SummariesJob {job_id}] Failed to trigger next batch: {trigger_error}")
                # Mark job as failed if we can't continue
                update_summaries_job(job_id, enterprise_name, status="FAILED", error=f"Failed to trigger next batch: {str(trigger_error)}")
        else:
            # All chunks processed - mark job as completed
            update_summaries_job(
                job_id, enterprise_name,
                status="COMPLETED",
                processed_profiles=processed,
                success_count=total_success,
                skipped_count=total_skipped,
                error_count=total_errors
            )
            
            logger.info(f"[SummariesJob {job_id}] ✅ Completed: {total_success} success, {total_skipped} skipped, {total_errors} errors")
    
    except Exception as e:
        logger.error(f"[SummariesJob {job_id}] Failed: {str(e)}", exc_info=True)
        update_summaries_job(job_id, enterprise_name, status="FAILED", error=str(e))


@router.post("/api/v1/audience-rooms/{audience_room_id}/generate-summaries/async")
async def trigger_async_summaries(
    audience_room_id: str = Path(...),
    enterpriseName: Optional[str] = Query(None),
    chunkSize: int = Query(CHUNK_SIZE, ge=1, le=20),
    taskToken: Optional[str] = Query(None),
    background_tasks: BackgroundTasks = None,
    request: Request = None
):
    """
    Trigger async profile summaries generation that runs in the background.
    Returns immediately with a job_id for polling status.
    
    Job state is stored in DATABASE (SummariesJob table) - survives Vercel cold starts.
    Uses self-triggering chunk processing to avoid Vercel 13-minute timeout.
    
    Path Parameters:
    - audience_room_id: UUID of the audience room
    
    Query Parameters:
    - enterpriseName (optional): Enterprise name for database selection
    - chunkSize (optional): Profiles per chunk (default: 10, max: 20)
    - taskToken (optional): Step Functions callback token
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
    
    # Create job record in DATABASE
    job = create_summaries_job(
        job_id=job_id,
        audience_room_id=audience_room_id,
        task_token=taskToken,
        enterprise_name=enterpriseName
    )
    
    # Get base URL for self-triggering (from request or environment)
    base_url = get_base_url()
    if request:
        try:
            base_url = str(request.base_url).rstrip('/')
        except Exception:
            pass  # Fall back to environment variable
    
    # Add background task to process first batch of chunks (starting from chunk 0)
    background_tasks.add_task(
        process_summaries_chunks,
        job_id=job_id,
        audience_room_id=audience_room_id,
        enterprise_name=enterpriseName,
        chunk_size=chunkSize,
        start_chunk=0,
        base_url=base_url
    )
    
    logger.info(f"=== ASYNC SUMMARIES TRIGGERED: job_id={job_id} ===")
    
    return {
        "job_id": job_id,
        "status": "PENDING",
        "audience_room_id": audience_room_id,
        "message": "Profile summaries job started. Use /generate-summaries/async/status/{job_id} to check progress."
    }


@router.post("/api/v1/audience-rooms/{audience_room_id}/generate-summaries/async/process")
async def process_summaries_chunks_endpoint(
    audience_room_id: str = Path(...),
    jobId: str = Query(..., description="Job ID to continue processing"),
    startChunk: int = Query(0, description="Chunk number to start from"),
    chunkSize: int = Query(CHUNK_SIZE, description="Profiles per chunk"),
    enterpriseName: Optional[str] = Query(None),
    background_tasks: BackgroundTasks = None,
    request: Request = None
):
    """
    Internal endpoint to process next batch of chunks (self-triggered).
    This is called by the previous batch to continue processing.
    """
    logger.info(f"Processing chunks for job {jobId} starting from chunk {startChunk}")
    
    # Get base URL for self-triggering (from request or environment)
    base_url = get_base_url()
    if request:
        try:
            base_url = str(request.base_url).rstrip('/')
        except Exception:
            pass  # Fall back to environment variable
    
    # Add background task to process chunks
    background_tasks.add_task(
        process_summaries_chunks,
        job_id=jobId,
        audience_room_id=audience_room_id,
        enterprise_name=enterpriseName,
        chunk_size=chunkSize,
        start_chunk=startChunk,
        base_url=base_url
    )
    
    return {
        "job_id": jobId,
        "status": "PROCESSING",
        "message": f"Processing chunks starting from {startChunk}"
    }


@router.get("/api/v1/audience-rooms/{audience_room_id}/generate-summaries/async/status/{job_id}")
async def get_async_summaries_status(
    audience_room_id: str = Path(...),
    job_id: str = Path(..., description="Job ID returned from trigger endpoint"),
    enterpriseName: Optional[str] = Query(None)
):
    """
    Check the status of an async profile summaries job from DATABASE.
    
    Returns current progress including:
    - status: PENDING, PROCESSING, COMPLETED, FAILED
    - total_profiles: Total profiles to process
    - processed_profiles: Number of profiles processed so far
    - current_chunk / total_chunks: Chunk progress
    - success_count, skipped_count, error_count: Result counts
    """
    logger.info(f"Checking async summaries status for job_id={job_id}")
    
    job = get_summaries_job(job_id, enterpriseName)
    
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    
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


@router.get("/api/v1/summaries/async/pending")
async def get_pending_summaries_jobs_endpoint(
    enterpriseName: Optional[str] = Query(None, description="Enterprise name")
):
    """
    Get all pending/processing summaries jobs.
    Useful for the poller Lambda to find jobs to monitor.
    """
    jobs = get_pending_summaries_jobs(enterpriseName)
    return {"jobs": jobs, "count": len(jobs)}
