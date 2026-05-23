"""Subreddit User Extraction service package."""
from app.services.subreddit_user_extraction_service.subreddit_user_extraction_handler import SubredditUserExtractionHandler
from app.services.subreddit_user_extraction_service.config import ExtractionConfig
from app.services.subreddit_user_extraction_service.schemas import SubredditBatchExtractionRequest

# Create global instance
request_handler = SubredditUserExtractionHandler()

__all__ = [
    "SubredditUserExtractionHandler",
    "ExtractionConfig",
    "SubredditBatchExtractionRequest",
    "request_handler",
]
