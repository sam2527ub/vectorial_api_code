"""Audience Group Summarization service - generate group summary and traits from profile summaries."""
from app.services.audience_group_summarization_service.audience_group_summarization_handler import (
    AudienceGroupSummarizationHandler,
)
from app.services.audience_group_summarization_service.config import (
    AudienceGroupSummarizationConfig,
    REQUIRED_TRAIT_TITLES,
)

request_handler = AudienceGroupSummarizationHandler()

__all__ = [
    "AudienceGroupSummarizationHandler",
    "AudienceGroupSummarizationConfig",
    "request_handler",
    "REQUIRED_TRAIT_TITLES",
]
