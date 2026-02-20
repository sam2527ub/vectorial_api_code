"""Interface for search client (Parallel API)."""
from abc import ABC, abstractmethod
from typing import Any, Dict, List


class SearchClientInterface(ABC):
    """Interface for Parallel API search client (people/LinkedIn)."""

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True if the client is properly configured."""
        pass

    @abstractmethod
    def get_raw_client(self) -> Any:
        """Return the underlying client if needed."""
        pass

    @abstractmethod
    async def start_scraping_job(
        self,
        query: str,
        model: str,
        match_limit: int,
        entity_type: str = "people",
    ) -> Dict[str, Any]:
        """Start a parallel search job and return run identifier (findall_id)."""
        pass

    @abstractmethod
    async def get_job_status(self, job_id: str) -> Dict[str, Any]:
        """Get status of a parallel search run from the API."""
        pass

    @abstractmethod
    async def fetch_job_results(self, job_id: str) -> List[Dict[str, Any]]:
        """Fetch profile results from a completed run (LinkedIn profiles)."""
        pass
