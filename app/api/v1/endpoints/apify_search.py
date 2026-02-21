"""Apify LinkedIn Company Employees search endpoints (async trigger + status polling)."""
import re
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Path, Query

from app.config import logger
from app.models.schemas import ApifySearchRequest
from app.services.web_indexing_service import request_handler as web_indexing_handler

router = APIRouter()


def _company_to_linkedin_url(company: str) -> str:
    """If input is not already a LinkedIn company URL, convert company name to linkedin.com/company/<slug>/."""
    s = (company or "").strip()
    if not s:
        return s
    s_lower = s.lower()
    if s_lower.startswith("http://") or s_lower.startswith("https://") or "linkedin.com/company" in s_lower:
        return s
    # Build slug: lowercase, spaces to hyphens, keep alphanumeric and hyphens
    slug = s.lower().replace(" ", "-")
    slug = re.sub(r"[^a-z0-9\-]", "", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    if not slug:
        slug = s.lower().replace(" ", "-").replace("&", "and")
        slug = re.sub(r"[^a-z0-9\-]", "", slug).strip("-") or "company"
    return f"https://www.linkedin.com/company/{slug}/"


def _normalize_companies(companies: List[str]) -> List[str]:
    """Return list with each item as a LinkedIn company URL (convert names to URLs when needed)."""
    seen: set = set()
    out: List[str] = []
    for c in companies:
        c = (c or "").strip()
        if not c:
            continue
        url = _company_to_linkedin_url(c)
        if url and url not in seen:
            seen.add(url)
            out.append(url)
    return out


def _apify_request_to_params(payload: ApifySearchRequest) -> Dict[str, Any]:
    """Build Apify actor input (camelCase). Only include keys with real values (no empty lists, no 0/placeholder)."""
    out: Dict[str, Any] = {}

    # Required – normalize so company names become LinkedIn URLs
    out["companies"] = _normalize_companies(payload.companies)

    # Optional – only include when explicitly set / non-empty
    if payload.company_batch_mode is not None:
        out["companyBatchMode"] = payload.company_batch_mode
    if payload.job_titles:
        out["jobTitles"] = payload.job_titles
    if payload.locations:
        out["locations"] = payload.locations
    if payload.profile_scraper_mode is not None:
        out["profileScraperMode"] = payload.profile_scraper_mode
    if payload.max_items is not None:
        out["maxItems"] = payload.max_items
    if payload.start_page is not None and payload.start_page >= 1:
        out["startPage"] = payload.start_page
    if payload.recently_changed_jobs is not None:
        out["recentlyChangedJobs"] = payload.recently_changed_jobs
    if payload.general_search_query and payload.general_search_query.strip():
        out["generalSearchQuery"] = payload.general_search_query.strip()
    if payload.industry_ids:
        out["industryIds"] = payload.industry_ids
    if payload.years_at_company:
        out["yearsAtCompany"] = payload.years_at_company

    return out


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
