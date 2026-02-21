"""Apify LinkedIn Company Employees search endpoints (async trigger + status polling)."""
from typing import Any, Dict, Optional
from fastapi import APIRouter, HTTPException, Path, Query

from app.config import logger
from app.models.schemas import ApifySearchRequest
from app.services.web_indexing_service import request_handler as web_indexing_handler

router = APIRouter()


def _apify_request_to_params(payload: ApifySearchRequest) -> Dict[str, Any]:
    """Build Apify actor input (camelCase) from API request."""
    params = {
        "companies": payload.companies,
        "companyBatchMode": payload.company_batch_mode or "one_by_one",
        "jobTitles": payload.job_titles,
        "locations": payload.locations,
        "profileScraperMode": payload.profile_scraper_mode or "Short ($4 per 1k)",
        "maxItems": payload.max_items if payload.max_items is not None else 250,
        "startPage": payload.start_page,
        "recentlyChangedJobs": payload.recently_changed_jobs,
        "generalSearchQuery": payload.general_search_query,
        "industryIds": payload.industry_ids,
        "yearsAtCompany": payload.years_at_company,
    }
    return {k: v for k, v in params.items() if v is not None}


@router.post("/api/search/apify/async")
async def trigger_async_apify_search(
    payload: ApifySearchRequest,
    enterpriseName: Optional[str] = Query(None),
):
    """
    Trigger an async Apify web indexing job (LinkedIn Company Employees Scraper).

    Filter-based: companies (required), jobTitles, locations, etc.
    Returns job_id for polling status at GET /api/search/apify/status/{job_id}.
    """
    logger.info(
        "=== TRIGGER ASYNC APIFY SEARCH REQUEST START === "
        "companies=%s, enterprise=%s",
        len(payload.companies),
        enterpriseName,
    )
    try:
        params = _apify_request_to_params(payload)
        response = await web_indexing_handler.start_async_job(
            provider="apify",
            params=params,
            enterprise_name=enterpriseName,
        )
        logger.info("=== TRIGGER ASYNC APIFY SEARCH REQUEST SUCCESS ===")
        return response
    except HTTPException:
        raise
    except Exception as e:
        error_message = str(e)
        error_type = type(e).__name__
        logger.error("Error in async Apify search trigger [%s]: %s", error_type, error_message)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "Failed to trigger Apify web indexing job",
                "message": error_message,
                "error_type": error_type,
            },
        )


@router.get("/api/search/apify/status/{job_id}")
async def get_apify_search_status(
    job_id: str = Path(..., description="Job ID returned from POST /api/search/apify/async"),
    enterpriseName: Optional[str] = Query(
        None,
        description="Enterprise name (gamma, app, entelligence, beta). If not provided, uses default audience database.",
    ),
):
    """
    Check the status of an async Apify web indexing job.

    Returns same shape as Parallel status: status (COMPLETED | FAILED | PROCESSING),
    profiles (when completed), total_found, error (when failed).
    """
    logger.info("=== GET APIFY SEARCH STATUS REQUEST START === job_id=%s, enterprise=%s", job_id, enterpriseName)
    try:
        return await web_indexing_handler.get_job_status(
            provider="apify",
            job_id=job_id,
            enterprise_name=enterpriseName,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error getting Apify job status: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
