"""Factory for post scraper client."""
from typing import Optional

from app.services.user_observation_fetch_service.config import UserObservationFetchConfig
from .interface import PostScraperClientInterface
from .apify_post_scraper_client import ApifyPostScraperClient


def get_post_scraper_client(
    config: Optional[UserObservationFetchConfig] = None,
) -> PostScraperClientInterface:
    """Return the Apify post scraper client."""
    return ApifyPostScraperClient(config or UserObservationFetchConfig())
