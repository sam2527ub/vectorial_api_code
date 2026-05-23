"""Interface for scraping clients."""
from abc import ABC, abstractmethod
from typing import Any, Dict, List


class PostScrapingClientInterface(ABC):
    """Interface for scraping clients that scrape post URLs with comments."""

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True if the client is properly configured."""
        pass

    @abstractmethod
    def get_raw_client(self) -> Any:
        """Return the underlying SDK client."""
        pass

    @abstractmethod
    async def start_post_scraping_job(self, post_urls: List[str]) -> str:
        """Start a scraping job for post URLs and return job identifier."""
        pass

    @abstractmethod
    def get_job_status(self, job_id: str) -> Dict[str, Any]:
        """Get status of a scraping job."""
        pass

    @abstractmethod
    def fetch_job_results(self, job_id: str) -> List[Dict[str, Any]]:
        """Fetch results from a completed job."""
        pass
