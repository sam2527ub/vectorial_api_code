"""Group Summary Generation service package."""
from app.services.audience_group_summarization_service.group_summarization_handler import GroupSummarizationHandler
from app.services.audience_group_summarization_service.config import GroupSummaryConfig
from app.services.audience_group_summarization_service.utils.profile_summary_utils import ProfileSummaryFetcher
from app.services.audience_group_summarization_service.group_summary_generator import GroupSummaryGenerator
from app.services.audience_group_summarization_service.traits_generator import TraitsGenerator

# Create global instance
request_handler = GroupSummarizationHandler()

__all__ = [
    "GroupSummarizationHandler",
    "GroupSummaryConfig",
    "ProfileSummaryFetcher",
    "GroupSummaryGenerator",
    "TraitsGenerator",
    "request_handler",
]
