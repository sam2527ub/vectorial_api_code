"""User Profile Fetch service - sync Apify enrichment (Enrich Profiles Sync)."""
from app.services.user_profile_fetch_service.user_profile_fetch_handler import UserProfileFetchHandler
from app.services.user_profile_fetch_service.config import UserProfileFetchConfig

request_handler = UserProfileFetchHandler()

__all__ = [
    "UserProfileFetchHandler",
    "UserProfileFetchConfig",
    "request_handler",
]
