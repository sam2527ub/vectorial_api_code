"""Request/Response schemas for Web Indexing Service."""
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class ParallelScrapeRequest(BaseModel):
    """Request schema for triggering parallel scraping."""
    query: str = Field(..., description="Search query for web indexing")
    model: str = Field("core", description="Model to use: 'core' or 'base'")
    match_limit: int = Field(5, ge=1, le=1000, description="Maximum number of matches to return")
    entity_type: str = Field("subreddit", description="Type of entity to scrape")


class ParallelScrapeStatusResponse(BaseModel):
    """Response schema for parallel scrape status."""
    job_id: str
    status: str
    message: Optional[str] = None
    error: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
