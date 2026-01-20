"""Parallel AI search endpoints with SSE streaming."""
import os
import json
import asyncio
import httpx
from typing import Optional
from fastapi import APIRouter, HTTPException, Path, Query
from fastapi.responses import StreamingResponse
from app.models.schemas import ParallelSearchRequest, ParallelSearchPreviewRequest
from app.config import logger
from app.services.apify_service import fetch_linkedin_profile_info, extract_apify_profile_fields
from app import database
from app.utils.helpers import ensure_db_available
from datetime import datetime

router = APIRouter()


@router.post("/api/search/parallel")
async def search_parallel_stream(payload: ParallelSearchRequest):
    """
    Search for LinkedIn profiles using Parallel AI FindAll API with real-time SSE streaming.
    
    This endpoint:
    1. Starts a Parallel FindAll run
    2. Immediately connects to the Parallel SSE endpoint (no polling)
    3. Proxies and transforms events to frontend format
    4. For each LinkedIn URL found, fetches profile info from Apify scraper in parallel
    5. Streams profile info back in real-time via SSE
    """
    logger.info(f"=== PARALLEL SEARCH STREAM REQUEST START ===")
    logger.info(f"Request: query={payload.query}, model={payload.model}, match_limit={payload.match_limit}")
    
    parallel_api_key = os.getenv("PARALLEL_API_KEY")
    if not parallel_api_key:
        logger.error("PARALLEL_API_KEY not configured")
        raise HTTPException(
            status_code=503,
            detail="PARALLEL_API_KEY not configured. Please set the environment variable."
        )
    
    logger.info("PARALLEL_API_KEY check passed")
    
    parallel_base_url = "https://api.parallel.ai/v1beta/findall"
    
    # Headers for Parallel API
    headers = {
        "x-api-key": parallel_api_key,
        "Content-Type": "application/json",
        "parallel-beta": "findall-2025-09-15"
    }
    
    # Payload for starting the run
    run_payload = {
        "objective": payload.query,
        "entity_type": "people",
        "match_conditions": [
            {
                "name": "query_match",
                "description": payload.query
            }
        ],
        "generator": payload.model,
        "match_limit": payload.match_limit
    }
    
    async def stream_parallel_events():
        """Generator function that streams Parallel API events."""
        run_id = None
        profile_info_queue = asyncio.Queue()
        active_tasks = set()
        
        async def fetch_and_queue_profile_info(linkedin_url: str, profile_data: dict):
            """Fetch profile info from Apify and queue it for streaming."""
            try:
                profile_info = await fetch_linkedin_profile_info(linkedin_url)
                if profile_info and isinstance(profile_info, dict):
                    has_valid_data = (
                        profile_info.get("fullName") or 
                        profile_info.get("jobTitle") or 
                        profile_info.get("companyName") or
                        profile_info.get("about") or
                        profile_info.get("headline")
                    )
                    if has_valid_data:
                        await profile_info_queue.put({
                            "linkedin_url": linkedin_url,
                            "profile_info": profile_info,
                            "original_data": profile_data,
                            "is_valid": True
                        })
                        logger.info(f"Profile validated and queued: {linkedin_url}")
                    else:
                        logger.warning(f"Profile validation failed for {linkedin_url}: No valid data")
                else:
                    logger.warning(f"Profile validation failed for {linkedin_url}: Apify returned None")
            except Exception as e:
                logger.error(f"Error in fetch_and_queue_profile_info for {linkedin_url}: {e}")
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                logger.info(f"Starting Parallel FindAll run with query: {payload.query}")
                
                start_response = await client.post(
                    f"{parallel_base_url}/runs",
                    json=run_payload,
                    headers=headers
                )
                
                if start_response.status_code not in [200, 201]:
                    error_detail = start_response.text
                    logger.error(f"Failed to start Parallel run: {error_detail}")
                    yield f"data: {json.dumps({'type': 'error', 'message': f'Failed to start run: {error_detail}'})}\n\n"
                    return
                
                run_data = start_response.json()
                run_id = run_data.get("findall_id") or run_data.get("id") or run_data.get("run_id")
                
                if not run_id:
                    logger.error(f"No run ID in response: {run_data}")
                    yield f"data: {json.dumps({'type': 'error', 'message': 'No run ID returned from Parallel API'})}\n\n"
                    return
                
                logger.info(f"Parallel FindAll run started successfully: {run_id}")
                
                # Connect to SSE endpoint
                sse_headers = {
                    "x-api-key": parallel_api_key,
                    "Accept": "text/event-stream",
                    "parallel-beta": "findall-2025-09-15"
                }
                
                sse_url = f"{parallel_base_url}/runs/{run_id}/events"
                logger.info(f"Connecting to SSE stream: {sse_url}")
                
                async with client.stream(
                    "GET",
                    sse_url,
                    headers=sse_headers,
                    timeout=None
                ) as sse_response:
                    
                    if sse_response.status_code != 200:
                        error_detail = await sse_response.aread()
                        error_text = error_detail.decode('utf-8', errors='ignore') if error_detail else "Unknown error"
                        logger.error(f"Failed to connect to SSE stream: {error_text}")
                        yield f"data: {json.dumps({'type': 'error', 'message': f'Failed to connect to SSE stream: {error_text}'})}\n\n"
                        return
                    
                    buffer = ""
                    stream_completed = False
                    profile_output_queue = asyncio.Queue()
                    
                    async def process_profile_queue_continuously():
                        """Continuously process profile queue."""
                        while not stream_completed or not profile_info_queue.empty() or active_tasks:
                            try:
                                try:
                                    update = await asyncio.wait_for(profile_info_queue.get(), timeout=0.1)
                                except asyncio.TimeoutError:
                                    continue
                                
                                linkedin_url = update["linkedin_url"]
                                profile_info = update["profile_info"]
                                original_data = update["original_data"]
                                is_valid = update.get("is_valid", False)
                                
                                if not is_valid:
                                    logger.info(f"Skipping invalid/non-existent profile: {linkedin_url}")
                                    continue
                                
                                extracted_profile = extract_apify_profile_fields(profile_info) if profile_info else {}
                                
                                if not extracted_profile or (not extracted_profile.get("fullName") and not extracted_profile.get("jobTitle")):
                                    logger.warning(f"Skipping profile with insufficient data: {linkedin_url}")
                                    continue
                                
                                profile_update = {
                                    "type": "profile_update",
                                    "status": original_data.get("status", "matched"),
                                    "data": {
                                        "url": linkedin_url,
                                        "summary": original_data.get("data", {}).get("summary", ""),
                                        "reasoning": original_data.get("data", {}).get("reasoning", ""),
                                        "apify_data": extracted_profile
                                    }
                                }
                                await profile_output_queue.put(profile_update)
                                logger.info(f"Queued validated profile for real-time streaming: {linkedin_url}")
                            except Exception as e:
                                logger.error(f"Error in queue processor: {e}")
                                continue
                    
                    queue_processor_task = asyncio.create_task(process_profile_queue_continuously())
                    
                    async for chunk in sse_response.aiter_bytes():
                        # Check output queue first for validated profiles
                        while not profile_output_queue.empty():
                            try:
                                profile_update = profile_output_queue.get_nowait()
                                yield f"data: {json.dumps(profile_update)}\n\n"
                            except Exception:
                                break
                        
                        if not chunk:
                            continue
                        
                        try:
                            buffer += chunk.decode('utf-8', errors='replace')
                            
                            while '\n' in buffer:
                                line, buffer = buffer.split('\n', 1)
                                line = line.strip()
                                
                                if not line:
                                    continue
                                
                                # Handle SSE format
                                if line.startswith('event: '):
                                    continue
                                elif line.startswith('data: '):
                                    event_json = line[6:]
                                elif line.startswith('id: ') or line.startswith('retry: '):
                                    continue
                                else:
                                    event_json = line
                                
                                if not event_json:
                                    continue
                                
                                # Check output queue when processing events
                                while not profile_output_queue.empty():
                                    try:
                                        profile_update = profile_output_queue.get_nowait()
                                        yield f"data: {json.dumps(profile_update)}\n\n"
                                    except Exception:
                                        break
                                
                                try:
                                    event_data = json.loads(event_json)
                                    
                                    event_type = (
                                        event_data.get("type") or 
                                        event_data.get("event") or 
                                        event_data.get("event_type") or
                                        ""
                                    ).lower()
                                    
                                    # Handle candidate events
                                    if "candidate" in event_type:
                                        if "matched" in event_type or "unmatched" in event_type or "generated" in event_type:
                                            candidate = event_data.get("data", {})
                                            if "candidate" in candidate:
                                                candidate = candidate.get("candidate", {})
                                            
                                            linkedin_url = (
                                                candidate.get("linkedin_url") or 
                                                candidate.get("url") or 
                                                candidate.get("profile_url") or
                                                candidate.get("linkedin_profile_url") or
                                                ""
                                            )
                                            
                                            match_status = "matched" if "matched" in event_type else "unmatched"
                                            
                                            summary = (
                                                candidate.get("summary") or 
                                                candidate.get("description") or
                                                candidate.get("profile_summary") or
                                                candidate.get("bio") or
                                                ""
                                            )
                                            
                                            reasoning = (
                                                candidate.get("reasoning") or 
                                                candidate.get("basis") or
                                                candidate.get("match_reason") or
                                                candidate.get("explanation") or
                                                ""
                                            )
                                            
                                            transformed = {
                                                "type": "profile",
                                                "status": match_status,
                                                "data": {
                                                    "url": linkedin_url,
                                                    "summary": summary,
                                                    "reasoning": reasoning,
                                                    "apify_data": None
                                                }
                                            }
                                            
                                            if linkedin_url:
                                                logger.info(f"Validating candidate: {linkedin_url} ({match_status})")
                                                task = asyncio.create_task(
                                                    fetch_and_queue_profile_info(linkedin_url, transformed)
                                                )
                                                active_tasks.add(task)
                                                task.add_done_callback(active_tasks.discard)
                                    
                                    # Handle run_completed
                                    elif "run_completed" in event_type or "completed" in event_type:
                                        logger.info("Parallel run completed")
                                        stream_completed = True
                                        break
                                    
                                    # Handle run_failed
                                    elif "run_failed" in event_type or "failed" in event_type:
                                        error_msg = (
                                            event_data.get("data", {}).get("error") or 
                                            event_data.get("data", {}).get("message") or
                                            event_data.get("message") or
                                            event_data.get("error") or
                                            "Run failed"
                                        )
                                        logger.error(f"Parallel run failed: {error_msg}")
                                        yield f"data: {json.dumps({'type': 'error', 'message': error_msg})}\n\n"
                                        return
                                    
                                    # Handle ping events (keep-alive)
                                    elif event_type == "ping":
                                        continue
                                    
                                    else:
                                        logger.info(f"Received Parallel event - type: {event_type}")
                                        continue
                                        
                                except json.JSONDecodeError as e:
                                    logger.warning(f"Failed to parse event JSON: {event_json[:100]}..., error: {e}")
                                    continue
                                    
                        except UnicodeDecodeError:
                            continue
                        except Exception as e:
                            logger.error(f"Error processing SSE chunk: {e}")
                            continue
                    
                    # Wait for all active Apify tasks to complete
                    if active_tasks:
                        logger.info(f"Waiting for {len(active_tasks)} active Apify tasks to complete...")
                        try:
                            await asyncio.wait_for(
                                asyncio.gather(*active_tasks, return_exceptions=True),
                                timeout=300
                            )
                        except asyncio.TimeoutError:
                            logger.warning("Timeout waiting for Apify tasks to complete")
                    
                    await asyncio.sleep(0.5)
                    
                    if not queue_processor_task.done():
                        queue_processor_task.cancel()
                        try:
                            await queue_processor_task
                        except asyncio.CancelledError:
                            pass
                    
                    # Process any remaining validated profiles
                    while not profile_output_queue.empty():
                        try:
                            profile_update = profile_output_queue.get_nowait()
                            yield f"data: {json.dumps(profile_update)}\n\n"
                        except Exception:
                            break
                    
                    logger.info("SSE stream ended normally")
                    yield f"data: {json.dumps({'type': 'completed', 'message': 'Stream ended'})}\n\n"
                    
        except httpx.TimeoutException:
            logger.error("Request to Parallel API timed out")
            yield f"data: {json.dumps({'type': 'error', 'message': 'Request to Parallel API timed out'})}\n\n"
        except httpx.RequestError as e:
            logger.error(f"Request error connecting to Parallel API: {e}")
            yield f"data: {json.dumps({'type': 'error', 'message': f'Failed to connect to Parallel API: {str(e)}'})}\n\n"
        except Exception as e:
            logger.error(f"Unexpected error in Parallel search stream: {e}", exc_info=True)
            yield f"data: {json.dumps({'type': 'error', 'message': f'Unexpected error: {str(e)}'})}\n\n"
    
    return StreamingResponse(
        stream_parallel_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@router.post("/api/search/parallel/preview")
async def search_parallel_preview_stream(payload: ParallelSearchPreviewRequest):
    """
    Preview search for LinkedIn profiles using Parallel AI FindAll API with real-time SSE streaming.
    
    This endpoint is identical to /api/search/parallel but with fixed parameters:
    - model: "core" (fixed)
    - match_limit: 5 (fixed)
    """
    parallel_api_key = os.getenv("PARALLEL_API_KEY")
    if not parallel_api_key:
        raise HTTPException(
            status_code=503,
            detail="PARALLEL_API_KEY not configured. Please set the environment variable."
        )
    
    # Create a ParallelSearchRequest with fixed values
    fixed_payload = ParallelSearchRequest(
        query=payload.query,
        model="core",
        match_limit=5
    )
    
    # Reuse the main search function
    return await search_parallel_stream(fixed_payload)


@router.post("/api/search/parallel/async")
async def trigger_async_parallel_search(payload: ParallelSearchRequest, enterpriseName: Optional[str] = Query(None)):
    """
    Trigger an async parallel search job (non-blocking).
    
    Creates a job record and starts the Parallel AI FindAll run without waiting for completion.
    Returns job_id immediately for polling status.
    
    Query Parameters:
    - enterpriseName (optional): Enterprise name to determine which database to use:
        - "gamma" -> uses GAMMA_DATABASE_URL
        - "app" -> uses APP_DATABASE_URL
        - "entelligence" -> uses ENTELLIGENCE_DATABASE_URL
        - "beta" -> uses BETA_DATABASE_URL
        - If not provided, uses AUDIENCE_DATABASE_URL
    """
    logger.info(f"=== TRIGGER ASYNC PARALLEL SEARCH REQUEST START ===")
    logger.info(f"Request: query={payload.query}, model={payload.model}, match_limit={payload.match_limit}, enterprise={enterpriseName}")
    
    parallel_api_key = os.getenv("PARALLEL_API_KEY")
    if not parallel_api_key:
        logger.error("PARALLEL_API_KEY not configured")
        raise HTTPException(
            status_code=503,
            detail="PARALLEL_API_KEY not configured. Please set the environment variable."
        )
    
    # Check if database is available
    ensure_db_available("audience")
    logger.info("Database availability check passed")
    
    try:
        # Create job record in database
        logger.info(f"Creating parallel search job in database (enterprise={enterpriseName})")
        job = database.create_parallel_search_job(
            query=payload.query,
            model=payload.model,
            match_limit=payload.match_limit,
            enterprise_name=enterpriseName,
        )
        job_id = job.id
        
        logger.info(f"Successfully created job {job_id} in database (enterprise={enterpriseName})")
        
        # Start Parallel FindAll run (non-blocking - start and return immediately)
        parallel_base_url = "https://api.parallel.ai/v1beta/findall"
        headers = {
            "x-api-key": parallel_api_key,
            "Content-Type": "application/json",
            "parallel-beta": "findall-2025-09-15"
        }
        
        run_payload = {
            "objective": payload.query,
            "entity_type": "people",
            "match_conditions": [
                {
                    "name": "query_match",
                    "description": payload.query
                }
            ],
            "generator": payload.model,
            "match_limit": payload.match_limit
        }
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                logger.info(f"Starting Parallel FindAll run for job {job_id}")
                start_response = await client.post(
                    f"{parallel_base_url}/runs",
                    json=run_payload,
                    headers=headers
                )
                
                if start_response.status_code not in [200, 201]:
                    error_detail = start_response.text
                    logger.error(f"Failed to start Parallel run for job {job_id}: {error_detail}")
                    database.update_parallel_search_job(job_id, {
                        "status": "FAILED",
                        "error": f"Failed to start run: {error_detail}"
                    }, enterprise_name=enterpriseName)
                    raise HTTPException(
                        status_code=start_response.status_code,
                        detail=f"Failed to start Parallel run: {error_detail}"
                    )
                
                run_data = start_response.json()
                parallel_run_id = run_data.get("findall_id") or run_data.get("id") or run_data.get("run_id")
                
                if not parallel_run_id:
                    logger.error(f"No run ID in response for job {job_id}: {run_data}")
                    database.update_parallel_search_job(job_id, {
                        "status": "FAILED",
                        "error": "No run ID returned from Parallel API"
                    }, enterprise_name=enterpriseName)
                    raise HTTPException(
                        status_code=502,
                        detail="Parallel API did not return a run ID"
                    )
                
                logger.info(f"Parallel FindAll run started for job {job_id}: run_id={parallel_run_id}")
                
                # Update job with run ID and set status to PROCESSING
                database.update_parallel_search_job(job_id, {
                    "status": "PROCESSING",
                    "parallelRunId": parallel_run_id
                }, enterprise_name=enterpriseName)
                
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error starting Parallel run for job {job_id}: {e}", exc_info=True)
            database.update_parallel_search_job(job_id, {
                "status": "FAILED",
                "error": str(e)
            }, enterprise_name=enterpriseName)
            raise HTTPException(
                status_code=500,
                detail=f"Failed to start Parallel run: {str(e)}"
            )
        
        logger.info(f"=== TRIGGER ASYNC PARALLEL SEARCH REQUEST SUCCESS ===")
        
        return {
            "job_id": job_id,
            "status": "PENDING",
            "message": "Parallel search job started. Use /api/search/parallel/status/{job_id} to check progress."
        }
        
    except Exception as e:
        error_message = str(e)
        error_type = type(e).__name__
        logger.error(f"Error in async parallel search trigger [{error_type}]: {error_message}")
        
        raise HTTPException(status_code=500, detail={
            "error": "Failed to trigger parallel search job",
            "message": error_message,
            "error_type": error_type
        })


@router.get("/api/search/parallel/status/{job_id}")
async def get_parallel_search_status(
    job_id: str = Path(..., description="Job ID returned from /api/search/parallel/async"),
    enterpriseName: Optional[str] = Query(None, description="Enterprise name (gamma, app, entelligence, beta). If not provided, uses default audience database.")
):
    """
    Check the status of an async parallel search job.
    If job is PENDING or PROCESSING, checks Parallel API for completion.
    If completed, fetches results and updates database.
    
    Query Parameters:
    - enterpriseName (optional): Enterprise name to determine which database to use:
        - "gamma" -> uses GAMMA_DATABASE_URL
        - "app" -> uses APP_DATABASE_URL
        - "entelligence" -> uses ENTELLIGENCE_DATABASE_URL
        - "beta" -> uses BETA_DATABASE_URL
        - If not provided, uses AUDIENCE_DATABASE_URL
    """
    logger.info(f"=== GET PARALLEL SEARCH STATUS REQUEST START ===")
    logger.info(f"Request: job_id={job_id}, enterprise={enterpriseName}")
    
    # Check if database is available
    ensure_db_available("audience")
    logger.info("Database availability check passed")
    
    try:
        # Get job from database
        logger.info(f"Fetching job {job_id} from database (enterprise={enterpriseName})")
        job = database.find_parallel_search_job_by_id(job_id, enterprise_name=enterpriseName)
        
        if not job:
            logger.warning(f"Job {job_id} not found in database (enterprise={enterpriseName})")
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
        
        logger.info(f"Found job {job_id}: status={job.status}, parallel_run_id={job.parallelRunId}")
        
        # If already completed, return cached results
        if job.status == "COMPLETED":
            logger.info(f"Job {job_id} is already COMPLETED")
            profiles = job.profiles if isinstance(job.profiles, list) else []
            raw_results = job.result if isinstance(job.result, dict) else {}
            
            return {
                "job_id": job_id,
                "status": "COMPLETED",
                "profiles": profiles,
                "total_found": len(profiles) if profiles else 0,
                "raw_results": raw_results,
                "query": job.query,
                "model": job.model,
                "created_at": job.createdAt.isoformat() if job.createdAt else None,
                "updated_at": job.updatedAt.isoformat() if job.updatedAt else None
            }
        
        # If failed, return error
        if job.status == "FAILED":
            logger.info(f"Job {job_id} status is FAILED, returning error")
            return {
                "job_id": job_id,
                "status": "FAILED",
                "error": job.error,
                "created_at": job.createdAt.isoformat() if job.createdAt else None,
                "updated_at": job.updatedAt.isoformat() if job.updatedAt else None
            }
        
        # If PENDING or PROCESSING, check Parallel API status
        if not job.parallelRunId:
            logger.info(f"Job {job_id} has no parallelRunId, status={job.status}")
            return {
                "job_id": job_id,
                "status": job.status,
                "message": "Waiting for Parallel run to start..."
            }
        
        logger.info(f"Job {job_id} status is {job.status}, checking Parallel API run status for run_id={job.parallelRunId}")
        
        parallel_api_key = os.getenv("PARALLEL_API_KEY")
        if not parallel_api_key:
            raise HTTPException(status_code=503, detail="PARALLEL_API_KEY not configured")
        
        parallel_base_url = "https://api.parallel.ai/v1beta/findall"
        headers = {
            "x-api-key": parallel_api_key,
            "parallel-beta": "findall-2025-09-15"
        }
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Check Parallel run status
                logger.info(f"Fetching Parallel run status for run_id={job.parallelRunId}")
                run_response = await client.get(
                    f"{parallel_base_url}/runs/{job.parallelRunId}",
                    headers=headers
                )
                
                if run_response.status_code == 404:
                    logger.warning(f"Parallel run {job.parallelRunId} not found")
                    database.update_parallel_search_job(job_id, {
                        "status": "FAILED",
                        "error": f"Parallel run {job.parallelRunId} not found"
                    }, enterprise_name=enterpriseName)
                    return {
                        "job_id": job_id,
                        "status": "FAILED",
                        "error": f"Parallel run {job.parallelRunId} not found"
                    }
                
                run_response.raise_for_status()
                run_data = run_response.json()
                
                logger.info(f"Parallel API response: {json.dumps(run_data)}")  # Debug log
                
                # Determine status from Parallel API response
                # Handle both string and dict status responses from Parallel API
                status_value = run_data.get("status", "")
                
                # If status is a dict, extract the actual status string
                if isinstance(status_value, dict):
                    # Try common keys that might contain the status
                    run_status = (
                        status_value.get("status") or 
                        status_value.get("state") or 
                        status_value.get("value") or 
                        ""
                    )
                    run_status = str(run_status).upper() if run_status else ""
                    logger.info(f"Status was a dict, extracted: {run_status}")
                elif isinstance(status_value, str):
                    run_status = status_value.upper()
                else:
                    # Fallback: try to convert to string
                    run_status = str(status_value).upper() if status_value else ""
                    logger.warning(f"Status was unexpected type {type(status_value)}, converted to: {run_status}")
                
                logger.info(f"Determined Parallel API run status: {run_status} (original value type: {type(status_value)})")
                
                if run_status in ["SUCCEEDED", "COMPLETED"]:
                    logger.info(f"Parallel run succeeded for job {job_id}")
                    
                    # Fetch results from Parallel API SSE endpoint
                    profiles = []
                    raw_results = []
                    
                    try:
                        sse_headers = {
                            "x-api-key": parallel_api_key,
                            "Accept": "text/event-stream",
                            "parallel-beta": "findall-2025-09-15"
                        }
                        
                        sse_url = f"{parallel_base_url}/runs/{job.parallelRunId}/events"
                        logger.info(f"Fetching results from SSE stream: {sse_url}")
                        
                        async with client.stream("GET", sse_url, headers=sse_headers, timeout=None) as sse_response:
                            if sse_response.status_code != 200:
                                raise Exception(f"Failed to connect to SSE stream: {sse_response.status_code}")
                            
                            buffer = ""
                            async for chunk in sse_response.aiter_bytes():
                                if not chunk:
                                    continue
                                
                                try:
                                    buffer += chunk.decode('utf-8', errors='replace')
                                    
                                    while '\n' in buffer:
                                        line, buffer = buffer.split('\n', 1)
                                        line = line.strip()
                                        
                                        if not line or line.startswith('event:') or line.startswith('id:') or line.startswith('retry:'):
                                            continue
                                        
                                        if line.startswith('data: '):
                                            event_json = line[6:]
                                        else:
                                            event_json = line
                                        
                                        if not event_json:
                                            continue
                                        
                                        try:
                                            event_data = json.loads(event_json)
                                            event_type = (event_data.get("type") or event_data.get("event") or "").lower()
                                            
                                            # Handle profile/candidate events
                                            if "candidate" in event_type or "profile" in event_type:
                                                candidate = event_data.get("data", {})
                                                if "candidate" in candidate:
                                                    candidate = candidate.get("candidate", {})
                                                
                                                linkedin_url = (
                                                    candidate.get("linkedin_url") or 
                                                    candidate.get("url") or 
                                                    candidate.get("profile_url") or
                                                    ""
                                                )
                                                
                                                if linkedin_url and "linkedin.com" in linkedin_url:
                                                    # Fetch profile info from Apify
                                                    try:
                                                        profile_info = await fetch_linkedin_profile_info(linkedin_url)
                                                        extracted_profile = extract_apify_profile_fields(profile_info) if profile_info else {}
                                                        
                                                        profile = {
                                                            "linkedin_url": linkedin_url,
                                                            "profile_info": extracted_profile,
                                                            "match_status": "matched" if "matched" in event_type else "unmatched",
                                                            "summary": candidate.get("summary", ""),
                                                            "reasoning": candidate.get("reasoning", "")
                                                        }
                                                        profiles.append(profile)
                                                        raw_results.append(event_data)
                                                        logger.info(f"Collected profile: {linkedin_url}")
                                                    except Exception as profile_error:
                                                        logger.warning(f"Failed to fetch profile info for {linkedin_url}: {profile_error}")
                                                        # Still add the profile without Apify data
                                                        profile = {
                                                            "linkedin_url": linkedin_url,
                                                            "profile_info": {},
                                                            "match_status": "matched" if "matched" in event_type else "unmatched",
                                                            "summary": candidate.get("summary", ""),
                                                            "reasoning": candidate.get("reasoning", "")
                                                        }
                                                        profiles.append(profile)
                                                        raw_results.append(event_data)
                                            
                                            # Handle completion
                                            elif "completed" in event_type or "run_completed" in event_type:
                                                logger.info("Parallel run completed")
                                                break
                                                
                                        except json.JSONDecodeError as e:
                                            logger.warning(f"Failed to parse SSE event JSON: {e}")
                                            continue
                                            
                                    if "completed" in buffer.lower() or "run_completed" in buffer.lower():
                                        break
                                        
                                except Exception as chunk_error:
                                    logger.warning(f"Error processing SSE chunk: {chunk_error}")
                                    continue
                                    
                    except Exception as sse_error:
                        logger.error(f"Error fetching results from SSE stream: {sse_error}", exc_info=True)
                        # Continue - we'll update job status even without full results
                    
                    # Update job with results
                    logger.info(f"Updating job {job_id} to COMPLETED with {len(profiles)} profiles")
                    database.update_parallel_search_job(job_id, {
                        "status": "COMPLETED",
                        "profiles": profiles,
                        "result": {
                            "total_found": len(profiles),
                            "query": job.query,
                            "model": job.model,
                            "raw_results": raw_results
                        }
                    }, enterprise_name=enterpriseName)
                    
                    return {
                        "job_id": job_id,
                        "status": "COMPLETED",
                        "profiles": profiles,
                        "total_found": len(profiles),
                        "raw_results": raw_results,
                        "query": job.query,
                        "model": job.model,
                        "created_at": job.createdAt.isoformat() if job.createdAt else None,
                        "updated_at": datetime.utcnow().isoformat()
                    }
                    
                elif run_status in ["FAILED", "ERROR"]:
                    error_msg = run_data.get("error") or run_data.get("message") or "Unknown error"
                    logger.error(f"Parallel run failed for job {job_id}: {error_msg}")
                    database.update_parallel_search_job(job_id, {
                        "status": "FAILED",
                        "error": f"Parallel run failed: {error_msg}"
                    }, enterprise_name=enterpriseName)
                    return {
                        "job_id": job_id,
                        "status": "FAILED",
                        "error": error_msg
                    }
                    
                elif run_status in ["RUNNING", "PENDING", "PROCESSING"]:
                    return {
                        "job_id": job_id,
                        "status": "PROCESSING",
                        "parallel_status": run_status,
                        "message": "Parallel search in progress..."
                    }
                else:
                    return {
                        "job_id": job_id,
                        "status": "PROCESSING",
                        "parallel_status": run_status,
                        "message": f"Run status: {run_status}"
                    }
                    
        except httpx.TimeoutException:
            logger.error(f"Timeout checking Parallel status for job {job_id}")
            return {
                "job_id": job_id,
                "status": job.status,
                "message": "Timeout checking Parallel API status"
            }
        except Exception as parallel_error:
            logger.error(f"Error checking Parallel status for job {job_id}: {parallel_error}", exc_info=True)
            return {
                "job_id": job_id,
                "status": job.status,
                "message": f"Error checking Parallel API status: {str(parallel_error)}"
            }
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting job status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

