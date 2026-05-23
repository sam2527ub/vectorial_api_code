"""Request/Response schemas for user activity scraping."""
from typing import List, Dict, Any
from pydantic import BaseModel, Field


class UserActivityScrapeRequest(BaseModel):
    """Request schema for scraping Reddit user activities."""
    usernames: List[str] = Field(..., min_items=1, description="List of Reddit usernames (without u/ prefix)")
    include_comments: bool = Field(False, description="Whether to include comments (default: off, posts only)")
    max_items: int = Field(5000, ge=1, description="Maximum items to scrape")
    sort: str = Field("top", description="Sort order: top, hot, new, controversial")
    time: str = Field("all", description="Time filter: all, year, month, week, day, hour")
