"""Factory for comment scraper client."""
from app.services.user_comments_fetch_service.config import UserCommentsFetchConfig
from .interface import CommentScraperClientInterface
from .apify_comments_client import ApifyCommentsClient


def get_comment_scraper_client(
    config: UserCommentsFetchConfig | None = None,
) -> CommentScraperClientInterface:
    """Return the Apify comments scraper client."""
    return ApifyCommentsClient(config or UserCommentsFetchConfig())
