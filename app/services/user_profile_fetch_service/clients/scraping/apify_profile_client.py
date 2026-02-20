"""Apify client implementation for LinkedIn profile batch scraping."""
import asyncio
from typing import Any, Dict, List, Optional

from app.config import apify_client, logger
from app.services.user_profile_fetch_service.config import UserProfileFetchConfig
from app.services.user_profile_fetch_service.utils.url_normalizer import normalize_linkedin_urls
from .interface import ProfileScrapingClientInterface


class ApifyProfileScrapingClient(ProfileScrapingClientInterface):
    """Uses Apify LinkedIn Profile Scraper for batch profile fetch."""

    def __init__(self, config: Optional[UserProfileFetchConfig] = None):
        self.config = config or UserProfileFetchConfig()

    def is_configured(self) -> bool:
        return apify_client is not None

    def start_batch_run(self, linkedin_urls: List[str]) -> Optional[str]:
        """Start Apify batch run. URLs are normalized inside. Returns run ID or None."""
        if not apify_client:
            logger.error("Apify client not initialized")
            return None
        normalized_urls = normalize_linkedin_urls(linkedin_urls)
        run_input = {
            "profileUrls": normalized_urls,
            "proxy": {"useApifyProxy": True, "apifyProxyGroups": []},
        }
        logger.info(f"Starting Apify batch run for {len(normalized_urls)} profiles")
        try:
            run = apify_client.actor(self.config.actor_id).start(run_input=run_input)
            run_data = run.get("data", run) if isinstance(run, dict) else None
            if run_data and isinstance(run_data, dict):
                run_id = run_data.get("id")
                if run_id:
                    logger.info(f"Apify batch run started: {run_id}")
                    return run_id
            logger.error(f"Apify start returned unexpected response: {run}")
            return None
        except Exception as e:
            logger.error(f"Error starting Apify batch run: {e}")
            return None

    async def wait_for_run_completion(self, run_id: str) -> Dict[str, Any]:
        """Wait for Apify run to complete. Returns status, dataset_id (if SUCCEEDED), error (if failed)."""
        if not apify_client:
            return {"status": "ERROR", "error": "Apify client not initialized"}
        logger.info(f"Waiting for Apify run {run_id} to complete...")
        elapsed = 0
        max_wait = self.config.max_wait_time_per_batch_sec
        interval = self.config.wait_interval_sec
        while elapsed < max_wait:
            await asyncio.sleep(interval)
            elapsed += interval
            try:
                run_status = apify_client.run(run_id).get()
                status = (run_status.get("status") or "").upper()
                if status == "SUCCEEDED":
                    dataset_id = run_status.get("defaultDatasetId")
                    logger.info(f"Apify run {run_id} completed successfully in {elapsed}s")
                    return {"status": "SUCCEEDED", "dataset_id": dataset_id}
                if status in ("FAILED", "ABORTED", "TIMED-OUT"):
                    error_msg = run_status.get("statusMessage", "Unknown error")
                    logger.error(f"Apify run {run_id} failed: {error_msg}")
                    return {"status": status, "error": error_msg}
            except Exception as e:
                logger.error(f"Error checking Apify run status: {e}")
                return {"status": "ERROR", "error": str(e)}
        logger.warning(f"Apify run {run_id} timed out after {max_wait}s")
        return {"status": "TIMED-OUT", "error": f"Run timed out after {max_wait} seconds"}

    def get_run_results(self, dataset_id: str) -> List[Dict[str, Any]]:
        """Fetch all items from the run dataset."""
        if not apify_client or not dataset_id:
            return []
        try:
            return list(apify_client.dataset(dataset_id).iterate_items())
        except Exception as e:
            logger.error(f"Error getting Apify run results: {e}")
            return []
