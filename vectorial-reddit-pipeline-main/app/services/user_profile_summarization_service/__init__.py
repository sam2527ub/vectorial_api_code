"""User Profile Summarization service package."""
from app.services.user_profile_summarization_service.profile_summary_processor import ProfileProcessor
from app.services.user_profile_summarization_service.summary_coordinator import ProfileSummaryGenerator
from app.services.user_profile_summarization_service.intermediate_batch_processor import BatchProcessor
from app.services.user_profile_summarization_service.summary_job_handler import SummaryJobHandler
from app.services.user_profile_summarization_service.config import SummarizationConfig

# Create global instances for convenience
profile_processor = ProfileProcessor()
profile_summary_generator = ProfileSummaryGenerator()
job_handler = SummaryJobHandler()

__all__ = [
    "ProfileProcessor",
    "ProfileSummaryGenerator",
    "SummaryJobHandler",
    "BatchProcessor",
    "SummarizationConfig",
    "profile_processor",
    "profile_summary_generator",
    "job_handler",
]
