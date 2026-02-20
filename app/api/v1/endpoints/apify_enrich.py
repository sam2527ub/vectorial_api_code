"""Apify batch enrichment endpoints - Enrich Profiles Sync (uses User Profile Fetch service)."""
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import logger
from app.services.user_profile_fetch_service import request_handler as user_profile_fetch_handler

router = APIRouter()


class ApifyEnrichRequest(BaseModel):
    """Request schema for Apify enrichment (POST /api/apify/enrich)."""
    profiles: List[Dict[str, Any]]  # List of profiles with linkedin_url
    enterprise_name: Optional[str] = None


class ApifyEnrichResponse(BaseModel):
    """Response schema for sync Apify enrichment."""
    profiles: List[Dict[str, Any]]
    total_found: int
    filtered_count: int
    total_profiles: int
    message: str


@router.post("/api/apify/enrich")
async def enrich_profiles_sync(payload: ApifyEnrichRequest):
    """
    Synchronous Apify batch enrichment (blocking) - Enrich Profiles Sync.

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
    logger.info("=== SYNC APIFY ENRICHMENT REQUEST START ===")
    logger.info(f"Request: {len(payload.profiles)} profiles, enterprise={payload.enterprise_name}")

    try:
        response = await user_profile_fetch_handler.enrich_profiles_sync(
            profiles=payload.profiles,
            enterprise_name=payload.enterprise_name,
        )
        logger.info("=== SYNC APIFY ENRICHMENT REQUEST SUCCESS ===")
        return response
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in enrich_profiles_sync: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


