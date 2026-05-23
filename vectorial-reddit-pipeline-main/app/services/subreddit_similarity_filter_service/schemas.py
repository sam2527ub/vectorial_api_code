"""Request schemas for subreddit similarity filter."""
from typing import List, Dict, Optional
from pydantic import BaseModel, Field
from app.services.subreddit_similarity_filter_service.config import (
    CHUNK_SIZE_SUBREDDITS,
    DEFAULT_MIN_ACTIVITIES,
)


class SubredditFilterRequest(BaseModel):
    """Request schema for filtering users by subreddit similarity (legacy sync)."""
    matched_subreddits: List[str] = Field(
        ...,
        min_items=1,
        description="List of matched subreddit names from Step 1"
    )
    users: List[Dict] = Field(
        ...,
        description="List of user objects from Step 2"
    )
    activities: Optional[List[Dict]] = Field(
        default=None,
        description="List of activities (optional if activities_s3_url provided)"
    )
    activities_s3_url: Optional[str] = Field(
        default=None,
        description="S3 URL containing activities JSON (for large datasets)"
    )
    min_activities: int = Field(
        DEFAULT_MIN_ACTIVITIES,
        ge=1,
        description="Minimum activities after filtering"
    )


class CreateSubredditFilterJobBody(BaseModel):
    """Request body for creating a subreddit similarity filter job."""
    matched_subreddits: List[str] = Field(
        ..., alias="matched_subreddits",
        description="List of matched subreddit names",
    )
    users: List[Dict] = Field(
        ...,
        description="List of user objects",
    )
    activities: Optional[List[Dict]] = Field(
        default=None,
        description="List of activities (optional if activities_s3_url provided)",
    )
    activities_s3_url: Optional[str] = Field(
        default=None,
        description="S3 URL containing activities JSON (for large datasets)",
    )
    min_activities: int = Field(
        DEFAULT_MIN_ACTIVITIES, ge=1, alias="min_activities",
        description="Minimum activities after filtering",
    )
    enterprise_name: Optional[str] = Field(
        None, alias="enterpriseName",
        description="Enterprise name for database routing",
    )
    chunk_size: int = Field(
        CHUNK_SIZE_SUBREDDITS, ge=1, le=1000, alias="chunkSize",
        description="Subreddits per chunk (1–1000)",
    )

    model_config = {"populate_by_name": True}


class ProcessSubredditFilterChunkBody(BaseModel):
    """Request body for processing one chunk (internal / self-triggered)."""
    job_id: str = Field(..., alias="jobId", description="Job ID")
    start_subreddit_index: int = Field(
        0, alias="startSubredditIndex",
        description="Subreddit index to start from",
    )
    chunk_size: int = Field(
        CHUNK_SIZE_SUBREDDITS, ge=1, le=1000, alias="chunkSize",
        description="Subreddits per chunk",
    )
    enterprise_name: Optional[str] = Field(
        None, alias="enterpriseName",
        description="Enterprise name for DB routing",
    )

    model_config = {"populate_by_name": True}
