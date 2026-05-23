"""Factory for scraping clients."""
from .interface import PostScrapingClientInterface
from .apify_client import ApifyPostScrapingClient
from app.services.comment_context_service.config import CommentContextServiceConfig


def get_scraping_client(config: CommentContextServiceConfig = None) -> PostScrapingClientInterface:
    """Get scraping client instance. Returns ApifyPostScrapingClient by default."""
    return ApifyPostScrapingClient(config)
