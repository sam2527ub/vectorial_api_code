"""Apify batch enrichment endpoints with synchronous (blocking) pattern."""
import asyncio
import logging
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.config import logger, apify_client, PROFILE_SCRAPER_ACTOR_ID
from app.services.apify_service import extract_apify_profile_fields

router = APIRouter()

# Constants
MAX_PROFILES_PER_BATCH = 50  # Apify recommends batching for large requests
MAX_WAIT_TIME_PER_BATCH = 300  # 5 minutes max wait per batch
WAIT_INTERVAL = 3  # Check every 3 seconds


def chunk_list(lst: List, chunk_size: int) -> List[List]:
    """Split a list into chunks of specified size."""
    return [lst[i:i + chunk_size] for i in range(0, len(lst), chunk_size)]


class ApifyEnrichRequest(BaseModel):
    """Request schema for Apify enrichment."""
    profiles: List[Dict[str, Any]]  # List of profiles with linkedin_url
    enterprise_name: Optional[str] = None


class ApifyEnrichResponse(BaseModel):
    """Response schema for sync Apify enrichment."""
    profiles: List[Dict[str, Any]]  # Enriched profiles
    total_found: int  # Number of successfully enriched profiles
    filtered_count: int  # Number of profiles that couldn't be enriched
    total_profiles: int  # Total profiles requested
    message: str


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
        "proxy": {"useApifyProxy": True, "apifyProxyGroups": []}
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


async def wait_for_apify_run_completion(apify_run_id: str) -> Dict[str, Any]:
    """
    Wait for an Apify run to complete (async blocking).
    
    Returns:
        Dict with status, dataset_id (if succeeded), and error (if failed)
    """
    if not apify_client:
        return {"status": "ERROR", "error": "Apify client not initialized"}
    
    logger.info(f"Waiting for Apify run {apify_run_id} to complete...")
    elapsed = 0
    
    while elapsed < MAX_WAIT_TIME_PER_BATCH:
        await asyncio.sleep(WAIT_INTERVAL)
        elapsed += WAIT_INTERVAL
        
        try:
            run_status = apify_client.run(apify_run_id).get()
            status = run_status.get("status", "").upper()
            
            if status == "SUCCEEDED":
                dataset_id = run_status.get("defaultDatasetId")
                logger.info(f"Apify run {apify_run_id} completed successfully in {elapsed}s")
                return {"status": "SUCCEEDED", "dataset_id": dataset_id}
            elif status in ["FAILED", "ABORTED", "TIMED-OUT"]:
                error_msg = run_status.get("statusMessage", "Unknown error")
                logger.error(f"Apify run {apify_run_id} failed: {error_msg}")
                return {"status": status, "error": error_msg}
            # Otherwise still running, continue waiting
        except Exception as e:
            logger.error(f"Error checking Apify run status: {e}")
            return {"status": "ERROR", "error": str(e)}
    
    # Timeout
    logger.warning(f"Apify run {apify_run_id} timed out after {MAX_WAIT_TIME_PER_BATCH}s")
    return {"status": "TIMED-OUT", "error": f"Run timed out after {MAX_WAIT_TIME_PER_BATCH} seconds"}


@router.post("/api/apify/enrich")
async def enrich_profiles_sync(payload: ApifyEnrichRequest):
    """
    Synchronous Apify batch enrichment (blocking).
    
    Takes a list of profiles with linkedin_url and enriches them using Apify.
    Waits for all Apify runs to complete before returning enriched profiles.
    
    Profiles are automatically split into batches of 50 to avoid rate limits.
    The endpoint will wait for all batches to complete (up to 5 minutes per batch).
    
    The profiles should contain at minimum:
    - linkedin_url: The LinkedIn profile URL to enrich
    
    Additional fields from Parallel search (name, description, etc.) will be preserved.
    
    Returns:
        - profiles: List of successfully enriched profiles
        - total_found: Number of successfully enriched profiles
        - filtered_count: Number of profiles that couldn't be enriched
    """
    logger.info(f"=== SYNC APIFY ENRICHMENT REQUEST START ===")
    logger.info(f"Request: {len(payload.profiles)} profiles, enterprise={payload.enterprise_name}")
    
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
    
    # Split URLs into batches to avoid rate limits
    url_batches = chunk_list(linkedin_urls, MAX_PROFILES_PER_BATCH)
    logger.info(f"Split {len(linkedin_urls)} profiles into {len(url_batches)} batches of max {MAX_PROFILES_PER_BATCH}")
    
    # Start all Apify batch runs
    batch_runs = []  # List of (batch_idx, apify_run_id, url_batch)
    
    for batch_idx, url_batch in enumerate(url_batches):
        logger.info(f"Starting Apify batch {batch_idx + 1}/{len(url_batches)} with {len(url_batch)} profiles")
        apify_run_id = start_apify_batch_run(url_batch)
        
        if apify_run_id:
            batch_runs.append((batch_idx, apify_run_id, url_batch))
            logger.info(f"Batch {batch_idx + 1} started: {apify_run_id}")
        else:
            logger.error(f"Failed to start Apify batch {batch_idx + 1}")
    
    if not batch_runs:
        raise HTTPException(status_code=500, detail="Failed to start any Apify batch runs")
    
    # Wait for all batches to complete and collect results
    all_apify_results = []
    failed_batches = []
    
    logger.info(f"Waiting for {len(batch_runs)} batches to complete...")
    
    for batch_idx, apify_run_id, url_batch in batch_runs:
        logger.info(f"Waiting for batch {batch_idx + 1}/{len(batch_runs)} (run {apify_run_id})...")
        
        # Wait for this batch to complete (blocking)
        status_result = await wait_for_apify_run_completion(apify_run_id)
        status = status_result.get("status", "")
        
        if status == "SUCCEEDED":
            dataset_id = status_result.get("dataset_id")
            if dataset_id:
                batch_results = get_apify_run_results(dataset_id)
                all_apify_results.extend(batch_results)
                logger.info(f"Batch {batch_idx + 1} completed: {len(batch_results)} profiles enriched")
            else:
                logger.warning(f"Batch {batch_idx + 1} succeeded but no dataset ID")
        else:
            error = status_result.get("error", "Unknown error")
            failed_batches.append({
                "batch_idx": batch_idx,
                "apify_run_id": apify_run_id,
                "error": error
            })
            logger.warning(f"Batch {batch_idx + 1} failed: {error}")
    
    # Map Apify results back to original profiles
    apify_url_map = {}
    for apify_profile in all_apify_results:
        profile_url = apify_profile.get("url", "") or apify_profile.get("linkedinUrl", "")
        if profile_url:
            normalized_url = profile_url.lower().rstrip('/')
            apify_url_map[normalized_url] = apify_profile
    
    logger.info(f"Got {len(apify_url_map)} unique Apify results to match against {len(url_to_profile_map)} profiles")
    
    # Enrich original profiles with Apify data
    enriched_profiles = []
    filtered_profiles = []
    
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
    
    logger.info(f"=== SYNC APIFY ENRICHMENT REQUEST SUCCESS ===")
    logger.info(f"Enriched {len(enriched_profiles)} profiles, filtered out {len(filtered_profiles)} invalid profiles")
    if failed_batches:
        logger.warning(f"{len(failed_batches)} batches failed: {failed_batches}")
    
    return {
        "profiles": enriched_profiles,
        "total_found": len(enriched_profiles),
        "filtered_count": len(filtered_profiles),
        "total_profiles": len(linkedin_urls),
        "message": f"Successfully enriched {len(enriched_profiles)} profiles. {len(filtered_profiles)} invalid profiles were filtered out."
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


