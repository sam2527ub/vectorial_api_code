"""Subreddit Similarity Filter service package."""
from app.services.subreddit_similarity_filter_service.subreddit_similarity_checker import (
    SimilarityChecker,
    is_ai_configured,
)
from app.services.subreddit_similarity_filter_service.subreddit_similarity_filter_handler import SubredditSimilarityFilterHandler
from app.services.subreddit_similarity_filter_service.schemas import (
    SubredditFilterRequest,
    CreateSubredditFilterJobBody,
    ProcessSubredditFilterChunkBody,
)
from app.services.subreddit_similarity_filter_service.config import SubredditFilterConfig
from app.services.ai_gateway_service import ai_gateway

# Create global instances
similarity_checker = SimilarityChecker() if is_ai_configured() else None
request_handler = SubredditSimilarityFilterHandler(similarity_checker) if similarity_checker else None


def initialize_ai_client():
    """Backward compatibility: return the AI gateway client."""
    return ai_gateway


def initialize_groq_client():
    """Backward compatibility: return gateway when configured, else None."""
    return ai_gateway if is_ai_configured() else None


__all__ = [
    "SimilarityChecker",
    "is_ai_configured",
    "SubredditSimilarityFilterHandler",
    "SubredditFilterRequest",
    "CreateSubredditFilterJobBody",
    "ProcessSubredditFilterChunkBody",
    "SubredditFilterConfig",
    "similarity_checker",
    "request_handler",
    "initialize_ai_client",
    "initialize_groq_client",
]
