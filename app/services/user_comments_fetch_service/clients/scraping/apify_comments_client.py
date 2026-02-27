"""Apify LinkedIn profile comments scraper client. Polls Apify directly for status and dataset."""
from typing import Any, Dict, List

from app.config import apify_client, logger
from app.services.user_comments_fetch_service.config import UserCommentsFetchConfig
from app.services.user_comments_fetch_service.utils.url_normalizer import normalize_profile_url
from .interface import CommentScraperClientInterface


class ApifyCommentsClient(CommentScraperClientInterface):
    """Uses Apify harvestapi/linkedin-profile-comments actor."""

    def __init__(self, config: UserCommentsFetchConfig):
        self.config = config

    def is_configured(self) -> bool:
        return apify_client is not None

    def start_run(
        self,
        profiles: List[str],
        max_items: int = 40,
        posted_limit: str = "any",
    ) -> Dict[str, Any]:
        """Start actor run. Returns { run_id, status, error }."""
        normalized = [u for u in (normalize_profile_url(p) for p in profiles) if u]
        if not normalized:
            return {"run_id": None, "status": "FAILED", "error": "No valid profile URLs"}
        run_input = {
            "profiles": normalized,
            "maxItems": max_items,
            "postedLimit": posted_limit or "any",
        }
        try:
            run = apify_client.actor(self.config.actor_id).start(run_input=run_input)
            run_data = run.get("data", run) if isinstance(run, dict) else {}
            run_id = run_data.get("id") if isinstance(run_data, dict) else None
            if not run_id:
                return {"run_id": None, "status": "FAILED", "error": "Apify did not return run id"}
            return {"run_id": run_id, "status": "RUNNING", "error": None}
        except Exception as e:
            logger.exception("Apify comments start_run failed")
            return {"run_id": None, "status": "FAILED", "error": str(e)}

    def get_run_status(self, run_id: str) -> Dict[str, Any]:
        """Poll Apify run. Returns { status, defaultDatasetId, error }."""
        if not apify_client:
            return {"status": "UNKNOWN", "defaultDatasetId": None, "error": "Apify client not initialized"}
        try:
            run = apify_client.run(run_id).get()
            run_data = run.get("data", run) if isinstance(run, dict) else run
            if not isinstance(run_data, dict):
                return {"status": "UNKNOWN", "defaultDatasetId": None, "error": "Unexpected response"}
            return {
                "status": run_data.get("status"),
                "defaultDatasetId": run_data.get("defaultDatasetId"),
                "error": run_data.get("statusMessage") or run_data.get("error"),
            }
        except Exception as e:
            logger.warning("get_run_status %s: %s", run_id, e)
            return {"status": "UNKNOWN", "defaultDatasetId": None, "error": str(e)}

    def get_dataset_items(self, dataset_id: str) -> List[Dict[str, Any]]:
        """Fetch all items from the run's dataset."""
        if not apify_client:
            return []
        try:
            dataset = apify_client.dataset(dataset_id)
            return list(dataset.iterate_items())
        except Exception as e:
            logger.exception("get_dataset_items %s: %s", dataset_id, e)
            return []
