"""User Post Classifier service - async classifier jobs (trigger, process, status)."""
from app.services.user_post_classifier_service.user_post_classifier_handler import (
    UserPostClassifierHandler,
)
from app.services.user_post_classifier_service.config import (
    UserPostClassifierConfig,
    DEFAULT_BATCH_SIZE,
    CLASSIFY_POSTS_BATCH_SIZE,
)

request_handler = UserPostClassifierHandler()

__all__ = [
    "UserPostClassifierHandler",
    "UserPostClassifierConfig",
    "request_handler",
    "DEFAULT_BATCH_SIZE",
    "CLASSIFY_POSTS_BATCH_SIZE",
]
