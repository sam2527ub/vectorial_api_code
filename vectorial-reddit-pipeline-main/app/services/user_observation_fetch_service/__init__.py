"""User Activity Scraping service package."""
from app.services.user_observation_fetch_service.user_activity_scraping_handler import UserActivityScrapingHandler
from app.services.user_observation_fetch_service.config import ScrapingConfig
from app.services.user_observation_fetch_service.schemas import UserActivityScrapeRequest
from app.services.user_observation_fetch_service.utils.username_utils import UsernameProcessor
from app.services.user_observation_fetch_service.status.status_handler import StatusHandler

# Create global instances
request_handler = UserActivityScrapingHandler()
status_handler = StatusHandler()

__all__ = [
    "UserActivityScrapingHandler",
    "ScrapingConfig",
    "UserActivityScrapeRequest",
    "UsernameProcessor",
    "StatusHandler",
    "request_handler",
    "status_handler",
]
