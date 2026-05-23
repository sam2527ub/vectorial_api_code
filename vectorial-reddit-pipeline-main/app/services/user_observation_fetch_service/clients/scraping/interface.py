"""Interface for scraping clients (e.g., Apify)."""
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


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
    async def start_scraping_job(
        self,
        username_batch: List[str],
        include_comments: bool,
        max_items: int,
        sort: str,
        time: str
    ) -> str:
        """Start a scraping job and return job identifier."""
        pass

    @abstractmethod
    def get_job_status(self, job_id: str) -> Dict[str, Any]:
        """Get status of a scraping job."""
        pass

    @abstractmethod
    def fetch_job_results(self, job_id: str) -> List[Dict[str, Any]]:
        """Fetch results from a completed job."""
        pass
