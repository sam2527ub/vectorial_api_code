"""Interface for search clients (Parallel API, Apify, etc.)."""
from abc import ABC, abstractmethod
from typing import Any, Dict, List


class SearchClientInterface(ABC):
    """Interface for web indexing search clients (people/LinkedIn)."""

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True if the client is properly configured."""
        pass

    @abstractmethod
    def get_raw_client(self) -> Any:
        """Return the underlying client if needed."""
        pass

    @abstractmethod
    async def start_scraping_job(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Start a scraping job. Returns dict with job identifier (e.g. findall_id or run_id).
        params: provider-specific (Parallel: query, model, match_limit, entity_type; Apify: companies, jobTitles, etc.).
        """
        pass

    @abstractmethod
    async def get_job_status(self, job_id: str) -> Dict[str, Any]:
        """Get status of a run from the provider API."""
        pass

    @abstractmethod
    async def fetch_job_results(self, job_id: str) -> List[Dict[str, Any]]:
        """Fetch profile results from a completed run (normalized to common shape)."""
        pass
