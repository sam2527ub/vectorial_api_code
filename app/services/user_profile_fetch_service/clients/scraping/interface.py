"""Interface for profile scraping client (e.g. Apify batch runs)."""
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class ProfileScrapingClientInterface(ABC):
    """Interface for starting batch runs and fetching profile scrape results."""

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True if the client is configured (e.g. API token set)."""
        pass

    @abstractmethod
    def start_batch_run(self, linkedin_urls: List[str]) -> Optional[str]:
        """Start a batch run for the given LinkedIn URLs. Returns run ID or None."""
        pass

    @abstractmethod
    async def wait_for_run_completion(self, run_id: str) -> Dict[str, Any]:
        """
        Wait for run to complete. Returns dict with status, dataset_id (if SUCCEEDED), error (if failed).
        """
        pass

    @abstractmethod
    def get_run_results(self, dataset_id: str) -> List[Dict[str, Any]]:
        """Fetch all items from the run's dataset. Returns list of profile items."""
        pass
