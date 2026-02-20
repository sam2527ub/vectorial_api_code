"""Request/response schemas for User Profile Fetch service (Enrich Profiles Sync)."""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class EnrichProfilesRequest(BaseModel):
    """Request body for sync Apify enrichment (POST /api/apify/enrich)."""
    profiles: List[Dict[str, Any]]  # List of profiles with linkedin_url
    enterprise_name: Optional[str] = None


class EnrichProfilesResponse(BaseModel):
    """Response shape for sync Apify enrichment."""
    profiles: List[Dict[str, Any]]
    total_found: int
    filtered_count: int
    total_profiles: int
    message: str
