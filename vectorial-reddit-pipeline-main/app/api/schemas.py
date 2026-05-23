"""Pydantic models for API request/response schemas."""
from typing import Optional, List, Literal
from pydantic import BaseModel, Field


class RedditBatchScrapeRequest(BaseModel):
    subreddits: List[str] = Field(..., min_items=1, description="List of subreddit names to scrape (without r/ prefix)")
    max_posts: Optional[int] = Field(None, ge=1, description="Number of posts to analyze per subreddit. Leave empty to scrape all posts.")
    max_comments: Optional[int] = Field(None, ge=1, description="Number of comments to analyze per subreddit. Leave empty to scrape all comments.")
    fetch_details: bool = Field(True, description="Whether to fetch additional details for each user. This will slow down the scraping process significantly.")
    max_users_per_subreddit: Optional[int] = Field(
        None,
        ge=1,
        description="Optional override for max users per subreddit. If omitted, backend default is used.",
    )

class CreateRedditAudienceRoomRequest(BaseModel):
    """Request schema for creating a Reddit audience room.
    
    Supports either:
    - activities: Direct array of activities (small datasets)
    - activities_s3_url: S3 URL containing activities JSON (large datasets)
    """
    audience_room_name: str = Field(..., description="Name of the audience room")
    audience_room_description: str = Field(..., description="Plain-text description for the audience room")
    userId: str = Field(..., description="User ID associated with this audience room")
    source: str = Field(..., description="Source of the audience room creation (e.g., 'Reddit')")
    query: str = Field(..., description="The search query used to find subreddits")
    search_results: List[dict] = Field(..., description="The full search results/subreddits from Step 1")
    users: List[dict] = Field(..., min_items=1, description="List of user objects from Step 4")
    activities: Optional[List[dict]] = Field(default=None, description="List of activities (optional if activities_s3_url provided)")
    activities_s3_url: Optional[str] = Field(default=None, description="S3 URL containing activities JSON (for large datasets)")
    enterpriseName: Optional[str] = Field(None, description="Enterprise name (gamma, app, entelligence, beta). If not provided, uses default audience database.")
    category: Optional[Literal["b2b", "b2c"]] = Field(None, description="Audience category for B2B/B2C dimension tagging. Stored on audience room in DB. If missing, DB field is left null (dimension tagging may default e.g. to b2c).")
    subreddit_similarity_data: Optional[dict] = Field(default=None, description="Subreddit similarity filter results from Step 4 (subreddit similarity filter endpoint)")
    # --- Preview-then-Real workflow additions ---
    audience_room_id: Optional[str] = Field(
        default=None,
        description=(
            "Optional pre-generated stable room id. Used by the Preview-then-Real "
            "Vercel Workflow so preview and real runs share one AudienceRoom row. "
            "When present, the endpoint runs in idempotent/replace mode."
        ),
    )
    is_preview: Optional[bool] = Field(
        default=False,
        description=(
            "When true, the call originates from the preview pipeline. The "
            "endpoint uses this as a safety guard: if the room is already "
            "status=READY, the destructive upsert is skipped."
        ),
    )
    is_preview_then_real: Optional[bool] = Field(
        default=False,
        description=(
            "True when this call participates in the Preview-then-Real flow. "
            "On the real pipeline, enables merging of preview-only profiles."
        ),
    )


class CreateRedditAudienceRoomShellRequest(BaseModel):
    """Pre-allocate a stable AudienceRoom row used by the Preview-then-Real
    Vercel Workflow. No profiles are attached; the row is marked with
    status=CREATING so downstream services can distinguish an in-flight
    room from a ready one."""
    audience_room_id: str = Field(..., description="Caller-provided stable UUID")
    audience_room_name: str = Field(..., description="Name of the audience room")
    audience_room_description: str = Field(..., description="Plain-text description for the audience room")
    userId: str = Field(..., description="User ID associated with this audience room")
    query: Optional[str] = Field(None, description="Optional search query")
    source: Optional[str] = Field(None, description="Optional source (e.g. Reddit)")
    enterpriseName: Optional[str] = Field(None, description="Enterprise name (gamma, app, entelligence, beta).")
    category: Optional[Literal["b2b", "b2c"]] = Field(None, description="Audience category.")


class AudienceRoomStatusUpdateRequest(BaseModel):
    """Payload for the /{id}/status endpoint. All fields are optional so the
    same shape can be reused for the UPGRADING / PREVIEW_READY / READY flips."""
    status: Optional[str] = Field(None, description="One of CREATING | PREVIEW_READY | UPGRADING | READY | FAILED")
    isPreview: Optional[bool] = Field(None, description="Whether the room still only holds preview data")
    markPreviewReady: Optional[bool] = Field(None, description="If true, set previewReadyAt=now")
    markFullReady: Optional[bool] = Field(None, description="If true, set fullReadyAt=now")
    enterpriseName: Optional[str] = Field(None, description="Enterprise name.")