"""Interface for post scraper client (Apify batch runs)."""
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class PostScraperClientInterface(ABC):
    """Interface for starting post-scraper batch runs and checking run status."""

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True if the client is configured."""
        pass

    @abstractmethod
    async def start_batch_run(
        self,
        urls: List[str],
        cookies_dict: List[dict],
        user_agent: str,
        max_posts: int,
    ) -> Dict[str, Any]:
        """Start a single batch run. Returns dict with apify_run_id, status, urls_count, error."""
        pass

    @abstractmethod
    def get_run_status(self, run_id: str) -> Dict[str, Any]:
        """Get Apify run status. Returns dict with status, defaultDatasetId, statusMessage."""
        pass
