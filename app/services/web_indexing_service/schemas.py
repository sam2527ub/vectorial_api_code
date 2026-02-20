"""Request/response schemas for Web Indexing Service (parallel search)."""
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class ParallelSearchRequestSchema(BaseModel):
    """Request schema for triggering async parallel search (people/LinkedIn)."""
    query: str = Field(..., description="Search query for Parallel FindAll")
    model: str = Field("core", description="Model to use: 'core' or 'base'")
    match_limit: int = Field(100, ge=1, le=1000, description="Maximum number of matches to return")
