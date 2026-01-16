"""Scraping endpoints."""
import logging
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, HTTPException, Path, Query
from app.models.schemas import ScrapeRequest
from app.config import apify_client, POST_SCRAPER_ACTOR_ID, logger
from app.utils.helpers import normalize_linkedin_url, ensure_db_available
from app.services.profile_service import process_posts_and_update_profiles
from app import database

router = APIRouter()


@router.post("/api/v1/scrape")
async def trigger_scraping(payload: ScrapeRequest):
    """
    Triggers Apify Actor (linkedin-post-search-scraper) asynchronously.
    Creates a job record and starts the Apify actor without waiting for completion.
    Returns job_id immediately for polling status.
    
    Request Body:
    - enterpriseName (optional): Enterprise name to determine which database to use:
        - "gamma" -> uses GAMMA_DATABASE_URL
        - "app" -> uses APP_DATABASE_URL
        - "entelligence" -> uses ENTELLIGENCE_DATABASE_URL
        - "beta" -> uses BETA_DATABASE_URL
        - If not provided, uses AUDIENCE_DATABASE_URL
    """
    # Convert Cookie Pydantic models to dictionaries for Apify
    cookies_dict = [cookie.dict(exclude_none=True) for cookie in payload.cookies]

    # Normalize LinkedIn URLs for database storage (for matching later)
    normalized_urls = [u for u in (normalize_linkedin_url(u) for u in payload.linkedin_urls) if u]
    
    # Prepare URLs for Apify - ensure they have full https://www.linkedin.com format
    apify_urls = []
    for url in payload.linkedin_urls:
        url = url.strip()
        # Ensure it has https://
        if not url.startswith("http://") and not url.startswith("https://"):
            url = "https://" + url
        # Ensure it has www. if it's just linkedin.com
        if url.startswith("https://linkedin.com") and not url.startswith("https://www.linkedin.com"):
            url = url.replace("https://linkedin.com", "https://www.linkedin.com")
        elif url.startswith("http://linkedin.com") and not url.startswith("http://www.linkedin.com"):
            url = url.replace("http://linkedin.com", "https://www.linkedin.com")
        apify_urls.append(url)

    run_input = {
        "urls": apify_urls,
        "limitPerSource": payload.max_posts,
        "cookie": cookies_dict,
        "userAgent": payload.user_agent,
        "proxy": {"useApifyProxy": True}
    }

    # Check if database is available
    ensure_db_available("audience")
    
    # Log enterpriseName for debugging
    logger.info(f"trigger_scraping called with enterpriseName={payload.enterpriseName}, audience_room_id={payload.audience_room_id}")
    
    try:
        # Create job record in database
        job = database.create_scrape_job(
            linkedin_urls=normalized_urls,
            max_posts=payload.max_posts,
            audience_room_id=payload.audience_room_id,
            enterprise_name=payload.enterpriseName,
        )
        job_id = job.id
        
        logger.info(f"Created job {job_id} for {len(payload.linkedin_urls)} URLs in database (enterpriseName={payload.enterpriseName})")
        
        # Start Apify actor without waiting (async)
        try:
            run = apify_client.actor(POST_SCRAPER_ACTOR_ID).start(run_input=run_input)
            run_data = None
            # Apify client may return {"data": {...}} or the payload directly; handle both
            if isinstance(run, dict):
                run_data = run.get("data", run)
            if not run_data or not isinstance(run_data, dict):
                raise HTTPException(status_code=502, detail="Apify start returned unexpected response structure")
            apify_run_id = run_data.get('id')
            if not apify_run_id:
                raise HTTPException(status_code=502, detail="Apify start did not return a run id")
            
            # Update job with Apify run ID and set status to PROCESSING
            database.update_scrape_job(job_id, {
                "status": "PROCESSING",
                "apifyRunId": apify_run_id
            }, enterprise_name=payload.enterpriseName)
            
            logger.info(f"Started Apify run {apify_run_id} for job {job_id}")
            
            return {
                "job_id": job_id,
                "status": "PENDING",
                "message": "Scraping job started. Use /api/v1/scrape/status/{job_id} to check progress."
            }
        except Exception as apify_error:
            # If Apify start fails, mark job as failed
            error_message = str(apify_error)
            database.update_scrape_job(job_id, {
                "status": "FAILED",
                "error": error_message
            }, enterprise_name=payload.enterpriseName)
            raise apify_error
            
    except Exception as e:
        error_message = str(e)
        error_type = type(e).__name__
        logger.error(f"Apify error [{error_type}]: {error_message}")
        
        # Handle specific error types - check multiple variations
        error_lower = error_message.lower()
        error_repr = repr(e).lower()
        
        # Check for usage/rate limit errors (check both message and repr)
        if any(keyword in error_lower or keyword in error_repr 
               for keyword in ["usage", "limit exceeded", "quota", "hard limit", "monthly usage"]):
            status_code = 429
            detail = {
                "error": "Apify usage limit exceeded",
                "message": error_message,
                "suggestion": "Please check your Apify account usage limits or upgrade your plan. You can check your usage at https://console.apify.com/usage"
            }
        # Check for authentication errors
        elif any(keyword in error_lower or keyword in error_repr 
                 for keyword in ["unauthorized", "authentication", "token", "invalid api", "api key"]):
            status_code = 401
            detail = {
                "error": "Apify authentication failed",
                "message": error_message,
                "suggestion": "Please check your APIFY_API_TOKEN environment variable."
            }
        # Check for actor not found errors
        elif any(keyword in error_lower or keyword in error_repr 
                 for keyword in ["not found", "actor", "404", "does not exist"]):
            status_code = 404
            detail = {
                "error": "Apify actor not found or inaccessible",
                "message": error_message,
                "suggestion": f"Please verify the actor ID: {POST_SCRAPER_ACTOR_ID}"
            }
        # Check for rate limiting (429 from Apify itself)
        elif "429" in error_message or "rate limit" in error_lower or "too many requests" in error_lower:
            status_code = 429
            detail = {
                "error": "Apify rate limit exceeded",
                "message": error_message,
                "suggestion": "Please wait a moment and try again, or check your Apify account rate limits."
            }
        # Generic server error
        else:
            status_code = 500
            detail = {
                "error": "Apify service error",
                "message": error_message,
                "error_type": error_type
            }
        
        raise HTTPException(status_code=status_code, detail=detail)


@router.get("/api/v1/scrape/status/{job_id}")
async def get_scrape_status(
    job_id: str = Path(..., description="Job ID returned from /api/v1/scrape"),
    enterpriseName: Optional[str] = Query(None, description="Enterprise name (gamma, app, entelligence, beta). If not provided, uses default audience database.")
):
    """
    Check the status of a scraping job.
    If job is PENDING or PROCESSING, checks Apify API for completion.
    If completed, fetches results and updates database.
    
    Query Parameters:
    - enterpriseName (optional): Enterprise name to determine which database to use:
        - "gamma" -> uses GAMMA_DATABASE_URL
        - "app" -> uses APP_DATABASE_URL
        - "entelligence" -> uses ENTELLIGENCE_DATABASE_URL
        - "beta" -> uses BETA_DATABASE_URL
        - If not provided, uses AUDIENCE_DATABASE_URL
    """
    # Check if database is available
    ensure_db_available("audience")
    
    # Log enterpriseName for debugging
    logger.info(f"get_scrape_status called with job_id={job_id}, enterpriseName={enterpriseName}")
    
    try:
        # Get job from database
        job = database.find_scrape_job_by_id(job_id, enterprise_name=enterpriseName)
        
        if not job:
            logger.warning(f"Job {job_id} not found in database (enterpriseName={enterpriseName})")
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
        
        logger.info(f"Found job {job_id} with status={job.status} (enterpriseName={enterpriseName})")
        
        # If already completed, attempt audience post backfill if dataset is available; otherwise return cached results
        if job.status == "COMPLETED":
            result_data = job.result if isinstance(job.result, dict) else {}
            dataset_id = None
            if result_data:
                dataset_id = (
                    result_data.get("dataset_id")
                    or result_data.get("defaultDatasetId")
                    or result_data.get("datasetId")
                )
            
            # If dataset_id not in result, try to fetch it from Apify using the stored run ID
            if not dataset_id and job.apifyRunId:
                try:
                    run = apify_client.run(job.apifyRunId).get()
                    run_data = run.get("data", run) if isinstance(run, dict) else {}
                    dataset_id = run_data.get("defaultDatasetId") if isinstance(run_data, dict) else None
                    logger.info(f"Fetched dataset_id {dataset_id} from Apify for completed job {job_id}")
                except Exception as apify_fetch_error:
                    logger.warning(f"Could not fetch dataset_id from Apify for job {job_id}: {apify_fetch_error}")

            # Try to process posts and update profiles if dataset is available
            if dataset_id and database.is_audience_db_available():
                try:
                    # Get LinkedIn URLs from job (handle different storage formats)
                    linkedin_urls_list = []
                    if job.linkedinUrls:
                        # JSON fields are returned as Python objects
                        if isinstance(job.linkedinUrls, list):
                            linkedin_urls_list = job.linkedinUrls
                        else:
                            # Try to convert to list if it's not already
                            try:
                                linkedin_urls_list = list(job.linkedinUrls) if hasattr(job.linkedinUrls, '__iter__') and not isinstance(job.linkedinUrls, str) else []
                            except (TypeError, ValueError):
                                linkedin_urls_list = []
                    
                    dataset_client = apify_client.dataset(dataset_id)
                    processing_result = await process_posts_and_update_profiles(
                        dataset_client=dataset_client,
                        job_id=job_id,
                        audience_room_id=job.audienceRoomId,
                        linkedin_urls=linkedin_urls_list if linkedin_urls_list else None,
                        enterprise_name=enterpriseName,
                    )
                    
                    # Update job result with processing info
                    new_result = {
                        "dataset_id": dataset_id,
                        **processing_result,
                    }
                    database.update_scrape_job(job_id, {"result": new_result}, enterprise_name=enterpriseName)
                    
                    return {
                        "job_id": job_id,
                        "status": "COMPLETED",
                        "audience_room_id": job.audienceRoomId,
                        "posts_found": processing_result.get("posts_found", 0),
                        "profiles_updated": processing_result.get("profiles_updated", 0),
                        "profiles_missing": processing_result.get("profiles_missing", 0),
                        "updated": processing_result.get("updated", []),
                        "missing": processing_result.get("missing", []),
                        "created_at": job.createdAt.isoformat() if job.createdAt else datetime.now().isoformat(),
                        "updated_at": datetime.now().isoformat(),
                    }
                except Exception as backfill_error:
                    logger.error(f"Audience backfill on completed job failed: {backfill_error}", exc_info=True)
                    # Don't fail the request, just return the existing completed status
                    # The job is already marked as COMPLETED, so we can return it

            return {
                "job_id": job_id,
                "status": "COMPLETED",
                "result": job.result if job.result else {},
                "created_at": job.createdAt.isoformat(),
                "updated_at": job.updatedAt.isoformat()
            }
        
        # If failed, return error
        if job.status == "FAILED":
            return {
                "job_id": job_id,
                "status": "FAILED",
                "error": job.error,
                "created_at": job.createdAt.isoformat(),
                "updated_at": job.updatedAt.isoformat()
            }
        
        # If PENDING or PROCESSING, check Apify status
        if not job.apifyRunId:
            return {
                "job_id": job_id,
                "status": job.status,
                "message": "Waiting for Apify run to start..."
            }
        
        try:
            # Check Apify run status
            run = apify_client.run(job.apifyRunId).get()
            run_data = run.get("data", run) if isinstance(run, dict) else {}
            run_status = run_data.get("status") if isinstance(run_data, dict) else None
            dataset_id = run_data.get("defaultDatasetId") if isinstance(run_data, dict) else None
            
            # If Apify didn't return a status but we already have a dataset, treat it as success
            if run_status == 'SUCCEEDED' or (not run_status and dataset_id):
                logger.info(f"Apify run succeeded for job {job_id}, dataset_id={dataset_id}, run_status={run_status} (enterpriseName={enterpriseName})")
                
                if not dataset_id:
                    return {
                        "job_id": job_id,
                        "status": "PROCESSING",
                        "message": "Run completed but dataset not available yet"
                    }

                dataset_client = apify_client.dataset(dataset_id)

                # Get LinkedIn URLs from job for matching profiles (handle different storage formats)
                linkedin_urls_list = []
                if job.linkedinUrls:
                    # Prisma JSON fields are typically returned as Python objects
                    if isinstance(job.linkedinUrls, list):
                        linkedin_urls_list = job.linkedinUrls
                    else:
                        # Try to convert to list if it's not already
                        try:
                            linkedin_urls_list = list(job.linkedinUrls) if hasattr(job.linkedinUrls, '__iter__') and not isinstance(job.linkedinUrls, str) else []
                        except (TypeError, ValueError):
                            linkedin_urls_list = []

                # Process posts and update profiles (works with or without audienceRoomId)
                # Wrap in try-except to ensure job status is ALWAYS updated even if processing fails
                processing_result = {"posts_found": 0, "profiles_updated": 0, "profiles_missing": 0, "updated": [], "missing": []}
                processing_error = None
                try:
                    processing_result = await process_posts_and_update_profiles(
                        dataset_client=dataset_client,
                        job_id=job_id,
                        audience_room_id=job.audienceRoomId,
                        linkedin_urls=linkedin_urls_list if linkedin_urls_list else None,
                        enterprise_name=enterpriseName,
                    )
                    logger.info(f"Successfully processed posts for job {job_id}: {processing_result.get('posts_found', 0)} posts found")
                except Exception as e:
                    processing_error = str(e)
                    logger.error(f"Error processing posts for job {job_id}: {e}", exc_info=True)
                    # Continue - we'll still update job status to COMPLETED

                # ALWAYS update job status to COMPLETED when Apify succeeds, even if processing had errors
                try:
                    result_data = {
                        "dataset_id": dataset_id,
                        **processing_result,
                    }
                    if processing_error:
                        result_data["processing_error"] = processing_error
                    
                    logger.info(f"Updating job {job_id} to COMPLETED status (enterpriseName={enterpriseName})")
                    updated_job = database.update_scrape_job(job_id, {
                        "status": "COMPLETED",
                        "result": result_data,
                    }, enterprise_name=enterpriseName)
                    
                    if not updated_job:
                        logger.error(f"Failed to update job {job_id} - update_scrape_job returned None (enterpriseName={enterpriseName})")
                    else:
                        logger.info(f"Successfully updated job {job_id} to COMPLETED (enterpriseName={enterpriseName})")
                        
                except Exception as update_error:
                    logger.error(f"Error updating job {job_id} status to COMPLETED: {update_error}", exc_info=True)
                    # If we can't update the status, return error info
                    return {
                        "job_id": job_id,
                        "status": "PROCESSING",
                        "message": f"Apify run completed but failed to update job status: {str(update_error)}",
                        "apify_status": run_status,
                        "error": str(update_error)
                    }

                return {
                    "job_id": job_id,
                    "status": "COMPLETED",
                    "posts_found": processing_result.get("posts_found", 0),
                    "audience_room_id": job.audienceRoomId,
                    "profiles_updated": processing_result.get("profiles_updated", 0),
                    "profiles_missing": processing_result.get("profiles_missing", 0),
                    "updated": processing_result.get("updated", []),
                    "missing": processing_result.get("missing", []),
                    "created_at": job.createdAt.isoformat() if job.createdAt else datetime.now().isoformat(),
                    "updated_at": datetime.now().isoformat(),
                    "processing_error": processing_error if processing_error else None,
                }
            elif run_status == 'FAILED':
                error_msg = run.get('data', {}).get('statusMessage', 'Unknown error')
                database.update_scrape_job(job_id, {
                    "status": "FAILED",
                    "error": f"Apify run failed: {error_msg}"
                }, enterprise_name=enterpriseName)
                return {
                    "job_id": job_id,
                    "status": "FAILED",
                    "error": error_msg
                }
            elif run_status in ['RUNNING', 'READY']:
                return {
                    "job_id": job_id,
                    "status": "PROCESSING",
                    "apify_status": run_status,
                    "message": "Scraping in progress..."
                }
            else:
                return {
                    "job_id": job_id,
                    "status": "PROCESSING",
                    "apify_status": run_status,
                    "message": f"Run status: {run_status}"
                }
        except Exception as apify_error:
            logger.error(f"Error checking Apify status for job {job_id}: {apify_error}")
            return {
                "job_id": job_id,
                "status": job.status,
                "message": f"Error checking Apify status: {str(apify_error)}"
            }
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting job status: {e}")
        raise HTTPException(status_code=500, detail=str(e))

