"""Interface for scraping clients (e.g., Apify)."""
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class ScrapingClientInterface(ABC):
    """Interface for scraping clients (e.g., Apify)."""

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True if the client is properly configured."""
        pass

    @abstractmethod
    def get_raw_client(self) -> Any:
        """Return the underlying SDK client."""
        pass

    @abstractmethod
    async def scrape_subreddit(
        self,
        subreddit: str,
        fetch_details: bool,
        max_posts: Optional[int] = None,
        max_comments: Optional[int] = None
    ) -> Dict[str, Any]:
        """Scrape a subreddit and return results."""
        pass
