"""Factory for creating scraping clients."""
from typing import Optional
from .interface import ScrapingClientInterface
from .apify_client import ApifyScrapingClient
from app.services.user_observation_fetch_service.config import ScrapingConfig


def get_scraping_client(config: Optional[ScrapingConfig] = None) -> ScrapingClientInterface:
    """Get scraping client instance. Returns ApifyScrapingClient by default."""
    # In future, this can switch on env vars:
    # e.g. SCRAPING_PROVIDER=apify|other
    return ApifyScrapingClient(config)
