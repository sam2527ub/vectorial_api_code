"""Factory for profile scraping client (Apify)."""
from typing import Optional

from app.services.user_profile_fetch_service.config import UserProfileFetchConfig
from .interface import ProfileScrapingClientInterface
from .apify_profile_client import ApifyProfileScrapingClient


def get_scraping_client(
    config: Optional[UserProfileFetchConfig] = None,
) -> ProfileScrapingClientInterface:
    """Return the Apify profile scraping client."""
    return ApifyProfileScrapingClient(config or UserProfileFetchConfig())
