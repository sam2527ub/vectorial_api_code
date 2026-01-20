"""Apify batch enrichment endpoints with async trigger/polling pattern."""
import os
import json
import uuid
import asyncio
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime
from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel
from app.config import logger, apify_client, PROFILE_SCRAPER_ACTOR_ID, s3_client, s3_bucket
from app.services.apify_service import extract_apify_profile_fields
from app.utils.s3_utils import upload_json_to_s3, fetch_json_from_s3

router = APIRouter()

# Constants
APIFY_JOB_PREFIX = "apify-enrichment-jobs"
MAX_PROFILES_PER_BATCH = 50  # Apify recommends batching for large requests


def chunk_list(lst: List, chunk_size: int) -> List[List]:
    """Split a list into chunks of specified size."""
    return [lst[i:i + chunk_size] for i in range(0, len(lst), chunk_size)]


class ApifyEnrichRequest(BaseModel):
    """Request schema for Apify enrichment."""
    profiles: List[Dict[str, Any]]  # List of profiles with linkedin_url
    enterprise_name: Optional[str] = None


class ApifyEnrichResponse(BaseModel):
    """Response schema for Apify enrichment trigger."""
    job_id: str
    status: str
    message: str
    total_profiles: int


def get_apify_job_s3_key(job_id: str) -> str:
    """Get S3 key for Apify job state."""
    return f"{APIFY_JOB_PREFIX}/{job_id}/job_state.json"


def get_apify_results_s3_key(job_id: str) -> str:
    """Get S3 key for Apify enrichment results."""
    return f"{APIFY_JOB_PREFIX}/{job_id}/results.json"


async def save_job_state_to_s3(job_id: str, state: Dict[str, Any]) -> str:
    """Save job state to S3."""
    key = get_apify_job_s3_key(job_id)
    return upload_json_to_s3(key, state)


async def get_job_state_from_s3(job_id: str) -> Optional[Dict[str, Any]]:
    """Get job state from S3."""
    try:
        key = get_apify_job_s3_key(job_id)
        return fetch_json_from_s3(key)
    except HTTPException as e:
        if e.status_code == 404:
            return None
        raise


async def save_results_to_s3(job_id: str, profiles: List[Dict[str, Any]]) -> str:
    """Save enriched profiles to S3."""
    key = get_apify_results_s3_key(job_id)
    return upload_json_to_s3(key, {"profiles": profiles, "total": len(profiles)})


def start_apify_batch_run(linkedin_urls: List[str]) -> Optional[str]:
    """
    Start Apify batch run for multiple LinkedIn URLs.
    
    Returns:
        Apify run ID if successful, None otherwise
    """
    if not apify_client:
        logger.error("Apify client not initialized")
        return None
    
    # Normalize URLs
    normalized_urls = []
    for url in linkedin_urls:
        url = url.strip()
        if not url.startswith("http://") and not url.startswith("https://"):
            url = "https://" + url
        if url.startswith("https://linkedin.com") and not url.startswith("https://www.linkedin.com"):
            url = url.replace("https://linkedin.com", "https://www.linkedin.com")
        normalized_urls.append(url)
    
    run_input = {
        "profileUrls": normalized_urls,
        "proxy": {"useApifyProxy": True}
    }
    
    logger.info(f"Starting Apify batch run for {len(normalized_urls)} profiles")
    
    try:
        run = apify_client.actor(PROFILE_SCRAPER_ACTOR_ID).start(run_input=run_input)
        run_data = None
        if isinstance(run, dict):
            run_data = run.get("data", run)
        
        if run_data and isinstance(run_data, dict):
            apify_run_id = run_data.get('id')
            if apify_run_id:
                logger.info(f"Apify batch run started: {apify_run_id}")
                return apify_run_id
        
        logger.error(f"Apify start returned unexpected response: {run}")
        return None
    except Exception as e:
        logger.error(f"Error starting Apify batch run: {e}")
        return None


def check_apify_run_status(apify_run_id: str) -> Dict[str, Any]:
    """
    Check status of Apify run.
    
    Returns:
        Dict with status, dataset_id (if completed), and error (if failed)
    """
    if not apify_client:
        return {"status": "ERROR", "error": "Apify client not initialized"}
    
    try:
        run_status = apify_client.run(apify_run_id).get()
        status = run_status.get("status", "").upper()
        
        result = {"status": status}
        
        if status == "SUCCEEDED":
            result["dataset_id"] = run_status.get("defaultDatasetId")
        elif status in ["FAILED", "ABORTED", "TIMED-OUT"]:
            result["error"] = run_status.get("statusMessage", "Unknown error")
        
        return result
    except Exception as e:
        logger.error(f"Error checking Apify run status: {e}")
        return {"status": "ERROR", "error": str(e)}


def get_apify_run_results(dataset_id: str) -> List[Dict[str, Any]]:
    """Get results from completed Apify run."""
    if not apify_client or not dataset_id:
        return []
    
    try:
        results = []
        for item in apify_client.dataset(dataset_id).iterate_items():
            results.append(item)
        return results
    except Exception as e:
        logger.error(f"Error getting Apify run results: {e}")
        return []


@router.post("/api/apify/enrich/async")
async def trigger_async_apify_enrichment(payload: ApifyEnrichRequest):
    """
    Trigger async Apify batch enrichment (non-blocking).
    
    Takes a list of profiles with linkedin_url and starts batch Apify enrichment.
    Returns job_id immediately for polling status.
    
    Profiles are automatically split into batches of 50 to avoid rate limits.
    
    The profiles should contain at minimum:
    - linkedin_url: The LinkedIn profile URL to enrich
    
    Additional fields from Parallel search (name, description, etc.) will be preserved.
    """
    logger.info(f"=== TRIGGER ASYNC APIFY ENRICHMENT REQUEST START ===")
    logger.info(f"Request: {len(payload.profiles)} profiles, enterprise={payload.enterprise_name}")
    
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 not configured for job storage")
    
    if not apify_client:
        raise HTTPException(status_code=503, detail="Apify client not initialized")
    
    if not payload.profiles:
        raise HTTPException(status_code=400, detail="No profiles provided")
    
    # Extract LinkedIn URLs from profiles
    linkedin_urls = []
    url_to_profile_map = {}  # Map URL to original profile data
    
    for profile in payload.profiles:
        linkedin_url = profile.get("linkedin_url", "")
        if linkedin_url and "linkedin.com" in linkedin_url:
            linkedin_urls.append(linkedin_url)
            url_to_profile_map[linkedin_url] = profile
    
    if not linkedin_urls:
        raise HTTPException(status_code=400, detail="No valid LinkedIn URLs found in profiles")
    
    # Generate job ID
    job_id = str(uuid.uuid4())
    
    # Split URLs into batches to avoid rate limits
    url_batches = chunk_list(linkedin_urls, MAX_PROFILES_PER_BATCH)
    logger.info(f"Split {len(linkedin_urls)} profiles into {len(url_batches)} batches of max {MAX_PROFILES_PER_BATCH}")
    
    # Start Apify batch runs for each chunk
    apify_run_ids = []
    batch_info = []
    
    for batch_idx, url_batch in enumerate(url_batches):
        logger.info(f"Starting Apify batch {batch_idx + 1}/{len(url_batches)} with {len(url_batch)} profiles")
        apify_run_id = start_apify_batch_run(url_batch)
        
        if apify_run_id:
            apify_run_ids.append(apify_run_id)
            batch_info.append({
                "batch_idx": batch_idx,
                "apify_run_id": apify_run_id,
                "urls": url_batch,
                "status": "PROCESSING"
            })
            logger.info(f"Batch {batch_idx + 1} started: {apify_run_id}")
        else:
            logger.error(f"Failed to start Apify batch {batch_idx + 1}")
            batch_info.append({
                "batch_idx": batch_idx,
                "apify_run_id": None,
                "urls": url_batch,
                "status": "FAILED",
                "error": "Failed to start Apify run"
            })
    
    if not apify_run_ids:
        raise HTTPException(status_code=500, detail="Failed to start any Apify batch runs")
    
    # Save job state to S3
    job_state = {
        "job_id": job_id,
        "status": "PROCESSING",
        "apify_run_ids": apify_run_ids,  # List of all batch run IDs
        "apify_run_id": apify_run_ids[0] if len(apify_run_ids) == 1 else None,  # For backward compatibility
        "batches": batch_info,
        "total_batches": len(url_batches),
        "completed_batches": 0,
        "total_profiles": len(linkedin_urls),
        "linkedin_urls": linkedin_urls,
        "url_to_profile_map": url_to_profile_map,
        "enterprise_name": payload.enterprise_name,
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat()
    }
    
    try:
        await save_job_state_to_s3(job_id, job_state)
        logger.info(f"Saved job state to S3 for job {job_id}")
    except Exception as e:
        logger.error(f"Failed to save job state to S3: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save job state: {str(e)}")
    
    logger.info(f"=== TRIGGER ASYNC APIFY ENRICHMENT REQUEST SUCCESS ===")
    logger.info(f"Job {job_id} started with {len(apify_run_ids)} Apify batches")
    
    return {
        "job_id": job_id,
        "status": "PROCESSING",
        "message": f"Apify enrichment started for {len(linkedin_urls)} profiles in {len(apify_run_ids)} batches. Use /api/apify/enrich/status/{job_id} to check progress.",
        "total_profiles": len(linkedin_urls),
        "total_batches": len(apify_run_ids)
    }


def is_valid_enriched_profile(profile: Dict[str, Any]) -> bool:
    """
    Check if a profile was successfully enriched by Apify.
    
    Returns False for profiles that:
    - Were not enriched (apify_enriched: false)
    - Have no valid profile_info data
    - Are clearly invalid LinkedIn profiles (Apify couldn't find them)
    """
    if not profile.get("apify_enriched", False):
        return False
    
    profile_info = profile.get("profile_info", {})
    if not profile_info:
        return False
    
    # Must have at least a name or headline to be considered valid
    has_name = bool(profile_info.get("fullName"))
    has_headline = bool(profile_info.get("headline"))
    has_job = bool(profile_info.get("jobTitle"))
    has_company = bool(profile_info.get("currentCompany"))
    
    # Profile is valid if it has a name and at least one other field
    return has_name and (has_headline or has_job or has_company)


@router.get("/api/apify/enrich/status/{job_id}")
async def get_apify_enrichment_status(
    job_id: str = Path(..., description="Job ID returned from /api/apify/enrich/async"),
    enterpriseName: Optional[str] = Query(None, description="Enterprise name (optional)")
):
    """
    Check the status of an async Apify enrichment job.
    
    If job is still processing, returns current status.
    If completed, returns ONLY successfully enriched profiles (invalid/non-existent profiles are filtered out).
    """
    logger.info(f"=== GET APIFY ENRICHMENT STATUS REQUEST START ===")
    logger.info(f"Request: job_id={job_id}")
    
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 not configured")
    
    # Get job state from S3
    job_state = await get_job_state_from_s3(job_id)
    
    if not job_state:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    
    # If already completed, return cached results
    if job_state.get("status") == "COMPLETED":
        logger.info(f"Job {job_id} is already COMPLETED")
        
        # Try to get results from S3
        try:
            results_key = get_apify_results_s3_key(job_id)
            results_data = fetch_json_from_s3(results_key)
            return {
                "job_id": job_id,
                "status": "COMPLETED",
                "profiles": results_data.get("profiles", []),
                "total_found": results_data.get("total", 0),
                "filtered_count": results_data.get("filtered_count", 0),
                "created_at": job_state.get("created_at"),
                "updated_at": job_state.get("updated_at")
            }
        except Exception as e:
            logger.warning(f"Failed to fetch results from S3: {e}")
            # Fallback to profiles in job_state if available
            return {
                "job_id": job_id,
                "status": "COMPLETED",
                "profiles": job_state.get("enriched_profiles", []),
                "total_found": len(job_state.get("enriched_profiles", [])),
                "created_at": job_state.get("created_at"),
                "updated_at": job_state.get("updated_at")
            }
    
    # If failed, return error
    if job_state.get("status") == "FAILED":
        return {
            "job_id": job_id,
            "status": "FAILED",
            "error": job_state.get("error", "Unknown error"),
            "created_at": job_state.get("created_at"),
            "updated_at": job_state.get("updated_at")
        }
    
    # Handle multi-batch jobs
    batches = job_state.get("batches", [])
    apify_run_ids = job_state.get("apify_run_ids", [])
    
    # Fall back to single run ID for backward compatibility
    if not apify_run_ids and job_state.get("apify_run_id"):
        apify_run_ids = [job_state.get("apify_run_id")]
        batches = [{
            "batch_idx": 0,
            "apify_run_id": job_state.get("apify_run_id"),
            "urls": job_state.get("linkedin_urls", []),
            "status": "PROCESSING"
        }]
    
    if not apify_run_ids:
        return {
            "job_id": job_id,
            "status": job_state.get("status", "PENDING"),
            "message": "Waiting for Apify runs to start..."
        }
    
    # Check status of all batches
    all_completed = True
    any_failed = False
    all_apify_results = []
    completed_batches = 0
    
    for batch in batches:
        apify_run_id = batch.get("apify_run_id")
        
        if not apify_run_id:
            # Batch failed to start
            continue
        
        if batch.get("status") == "COMPLETED":
            # Already processed this batch
            completed_batches += 1
            continue
        
        if batch.get("status") == "FAILED":
            continue
        
        # Check Apify status for this batch
        apify_status = check_apify_run_status(apify_run_id)
        status = apify_status.get("status", "")
        
        logger.info(f"Batch {batch.get('batch_idx', 0)} Apify run {apify_run_id} status: {status}")
        
        if status == "SUCCEEDED":
            batch["status"] = "COMPLETED"
            completed_batches += 1
            
            # Get results from this batch
            dataset_id = apify_status.get("dataset_id")
            if dataset_id:
                batch_results = get_apify_run_results(dataset_id)
                all_apify_results.extend(batch_results)
                batch["results_count"] = len(batch_results)
                logger.info(f"Batch {batch.get('batch_idx', 0)} returned {len(batch_results)} results")
        
        elif status in ["FAILED", "ABORTED", "TIMED-OUT", "ERROR"]:
            batch["status"] = "FAILED"
            batch["error"] = apify_status.get("error", "Unknown error")
            any_failed = True
            logger.warning(f"Batch {batch.get('batch_idx', 0)} failed: {batch['error']}")
        
        else:
            # Still processing
            all_completed = False
    
    # Update job state with batch progress
    job_state["batches"] = batches
    job_state["completed_batches"] = completed_batches
    job_state["updated_at"] = datetime.utcnow().isoformat()
    
    if all_completed or completed_batches == len(batches):
        # All batches completed (or failed)
        logger.info(f"All {len(batches)} batches completed for job {job_id}")
        
        # Map results back to original profiles and extract fields
        url_to_profile_map = job_state.get("url_to_profile_map", {})
        enriched_profiles = []
        filtered_profiles = []
        
        # Create a map of apify results by URL
        apify_url_map = {}
        for apify_profile in all_apify_results:
            profile_url = apify_profile.get("url", "") or apify_profile.get("linkedinUrl", "")
            if profile_url:
                # Normalize URL for matching
                normalized_url = profile_url.lower().rstrip('/')
                apify_url_map[normalized_url] = apify_profile
        
        logger.info(f"Got {len(apify_url_map)} unique Apify results to match against {len(url_to_profile_map)} profiles")
        
        # Enrich original profiles with Apify data
        for linkedin_url, original_profile in url_to_profile_map.items():
            # Find matching Apify result (try different URL formats)
            apify_data = None
            normalized_linkedin_url = linkedin_url.lower().rstrip('/')
            
            # Try exact match first
            if normalized_linkedin_url in apify_url_map:
                apify_data = apify_url_map[normalized_linkedin_url]
            else:
                # Try partial matching
                for apify_url, apify_profile in apify_url_map.items():
                    if normalized_linkedin_url in apify_url or apify_url in normalized_linkedin_url:
                        apify_data = apify_profile
                        break
            
            # Extract fields from Apify data
            extracted_profile = extract_apify_profile_fields(apify_data) if apify_data else {}
            
            # Merge with original profile
            enriched_profile = {
                **original_profile,
                "profile_info": extracted_profile,
                "apify_enriched": True if extracted_profile and extracted_profile.get("fullName") else False
            }
            
            # Filter: Only include successfully enriched profiles
            if is_valid_enriched_profile(enriched_profile):
                enriched_profiles.append(enriched_profile)
            else:
                filtered_profiles.append({
                    "linkedin_url": linkedin_url,
                    "reason": "Profile not found or invalid on LinkedIn"
                })
        
        logger.info(f"Enriched {len(enriched_profiles)} profiles, filtered out {len(filtered_profiles)} invalid profiles for job {job_id}")
        
        # Log filtered profiles for debugging
        if filtered_profiles:
            logger.info(f"Filtered profiles: {[fp['linkedin_url'] for fp in filtered_profiles[:5]]}{'...' if len(filtered_profiles) > 5 else ''}")
        
        # Update job state
        job_state["status"] = "COMPLETED"
        job_state["total_enriched"] = len(enriched_profiles)
        job_state["total_filtered"] = len(filtered_profiles)
        await save_job_state_to_s3(job_id, job_state)
        
        # Save results to S3 (only valid enriched profiles)
        results_data = {
            "profiles": enriched_profiles,
            "total": len(enriched_profiles),
            "filtered_count": len(filtered_profiles),
            "filtered_profiles": filtered_profiles
        }
        results_key = get_apify_results_s3_key(job_id)
        upload_json_to_s3(results_key, results_data)
        
        return {
            "job_id": job_id,
            "status": "COMPLETED",
            "profiles": enriched_profiles,
            "total_found": len(enriched_profiles),
            "filtered_count": len(filtered_profiles),
            "message": f"Successfully enriched {len(enriched_profiles)} profiles. {len(filtered_profiles)} invalid profiles were filtered out.",
            "created_at": job_state.get("created_at"),
            "updated_at": job_state.get("updated_at")
        }
    
    else:
        # Still processing
        await save_job_state_to_s3(job_id, job_state)
        
        return {
            "job_id": job_id,
            "status": "PROCESSING",
            "total_profiles": job_state.get("total_profiles", 0),
            "total_batches": len(batches),
            "completed_batches": completed_batches,
            "message": f"Apify enrichment in progress... ({completed_batches}/{len(batches)} batches completed)"
        }
