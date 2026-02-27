"""Comments scrape endpoints: start Apify run, poll status (no job table). Updates only AudienceProfile.commentsS3Url."""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.services.user_comments_fetch_service import UserCommentsFetchHandler
from app.services.user_comments_fetch_service.schemas import (
    CommentsScrapeRequest,
    CommentsScrapeResponse,
    CommentsScrapeStatusResponse,
)

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/api/v1/comments/scrape", response_model=CommentsScrapeResponse)
def start_comments_scrape(payload: CommentsScrapeRequest):
    """
    Start Apify LinkedIn profile comments scraper for an audience room.
    Profile URLs are loaded from the room; no LinkedIn URLs in the request.
    Returns run_id. Poll GET /api/v1/comments/scrape/status with same run_id and audience_room_id.
    """
    handler = UserCommentsFetchHandler()
    result = handler.start_run(
        audience_room_id=payload.audience_room_id,
        enterprise_name=payload.enterprise_name,
        max_items=payload.max_items,
        posted_limit=payload.posted_limit,
    )
    return CommentsScrapeResponse(**result)


@router.get("/api/v1/comments/scrape/status", response_model=CommentsScrapeStatusResponse)
def get_comments_scrape_status(
    audience_room_id: str = Query(..., description="Audience room ID – job is stored in S3 under this room"),
    enterprise_name: Optional[str] = Query(None, description="Enterprise for DB/S3 (same as POST if applicable)"),
):
    """
    Poll comment scrape status by audience_room_id. Job metadata is loaded from S3 (room's linkedin-comment-context/job.json).
    When SUCCEEDED, comment.json is uploaded per profile and commentsS3Url is updated.
    """
    if not audience_room_id:
        raise HTTPException(status_code=400, detail="audience_room_id is required")
    handler = UserCommentsFetchHandler()
    result = handler.check_status(
        audience_room_id=audience_room_id,
        enterprise_name=enterprise_name,
    )
    return CommentsScrapeStatusResponse(**result)
