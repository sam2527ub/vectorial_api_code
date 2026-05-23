# app/services/web_indexing_service/clients/search/interface.py
from abc import ABC, abstractmethod
from typing import Any, Dict, List


class SearchClientInterface(ABC):
    """Interface for search/API clients (e.g., Parallel API)."""

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True if the search client is properly configured."""
        pass

    @abstractmethod
    def get_raw_client(self) -> Any:
        """Return the underlying SDK client."""
        pass

    @abstractmethod
    async def start_scraping_job(
        self,
        query: str,
        model: str,
        match_limit: int,
        entity_type: str = "subreddit"
    ) -> Dict[str, Any]:
        """Start a scraping job and return job identifier."""
        pass

    @abstractmethod
    async def get_job_status(self, job_id: str) -> Dict[str, Any]:
        """Get status of a scraping job."""
        pass

    @abstractmethod
    async def fetch_job_results(self, job_id: str) -> List[Dict[str, Any]]:
        """Fetch results from a completed job."""
        pass
