"""Interface for comment scraper client (Apify LinkedIn profile comments)."""
from abc import ABC, abstractmethod
from typing import Any, Dict, List


class CommentScraperClientInterface(ABC):
    """Interface for starting comment scraper run and polling Apify for status/result."""

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True if the client is configured."""
        pass

    @abstractmethod
    def start_run(
        self,
        profiles: List[str],
        max_items: int = 40,
        posted_limit: str = "any",
    ) -> Dict[str, Any]:
        """Start Apify actor run. Returns dict with run_id, status, error."""
        pass

    @abstractmethod
    def get_run_status(self, run_id: str) -> Dict[str, Any]:
        """Poll Apify run. Returns dict with status, defaultDatasetId, error."""
        pass

    @abstractmethod
    def get_dataset_items(self, dataset_id: str) -> List[Dict[str, Any]]:
        """Fetch all items from Apify dataset. Returns list of raw comment items."""
        pass
