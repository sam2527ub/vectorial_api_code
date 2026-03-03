"""Pydantic schemas for User Comments Fetch service."""
from typing import Optional

from pydantic import BaseModel, Field


class CommentsScrapeRequest(BaseModel):
    """Request body for POST /api/v1/comments/scrape. LinkedIn URLs are derived from the room's profiles."""

    audience_room_id: str = Field(..., description="Audience room ID – profile URLs are loaded from this room and sent to Apify")
    max_items: int = Field(default=40, ge=0, description="Max comments per profile (default 40)")
    posted_limit: str = Field(default="any", description="Filter: any, day, week, month")
    enterprise_name: Optional[str] = Field(default=None, description="Enterprise for DB/S3")


class SlimComment(BaseModel):
    """Slim comment output: core fields plus optional AI context summary."""

    comment_url: str = Field(..., description="URL of the user comment")
    post_url: str = Field(..., description="URL of the post the user commented on")
    comment_body: str = Field(..., description="Body of the user comment")
    post_body: str = Field(..., description="Body of the post the user commented on")
    parent_comment_body: Optional[str] = Field(
        default=None, description="Body of parent comment if reply; null for top-level"
    )
    context_summary: Optional[str] = Field(
        default=None,
        description="AI-generated 1–2 sentence summary of post + parent comment context",
    )
    observationType: str = Field(
        default="comment",
        description="Observation type – always 'comment' for comment items",
    )


class CommentsScrapeResponse(BaseModel):
    """Response for POST /api/v1/comments/scrape. Poll status by audience_room_id."""

    job_id: str = Field(..., description="Job ID – poll GET /api/v1/comments/scrape/status?audience_room_id=... to get status")
    status: str = "PROCESSING"
    message: str = Field(..., description="Poll GET /api/v1/comments/scrape/status?audience_room_id=...")


class CommentsScrapeStatusResponse(BaseModel):
    """Response for GET /api/v1/comments/scrape/status (poll by audience_room_id)."""

    job_id: str
    status: str
    profiles_count: int = 0
    comments_found: Optional[int] = None
    profiles_updated: Optional[int] = None
    result: Optional[dict] = None
    error: Optional[str] = None
    batches_completed: Optional[int] = None
    batches_total: Optional[int] = None
