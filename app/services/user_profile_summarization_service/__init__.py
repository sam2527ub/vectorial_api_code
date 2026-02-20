"""User Profile Summarization service - async profile summaries (trigger, process chunks, status)."""
from app.services.user_profile_summarization_service.user_profile_summarization_handler import (
    UserProfileSummarizationHandler,
)
from app.services.user_profile_summarization_service.config import (
    UserProfileSummarizationConfig,
    CHUNK_SIZE,
    CHUNKS_PER_API_CALL,
    MAX_CONCURRENT,
)

request_handler = UserProfileSummarizationHandler()

__all__ = [
    "UserProfileSummarizationHandler",
    "UserProfileSummarizationConfig",
    "request_handler",
    "CHUNK_SIZE",
    "CHUNKS_PER_API_CALL",
    "MAX_CONCURRENT",
]
