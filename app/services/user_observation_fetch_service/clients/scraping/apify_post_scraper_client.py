"""Apify LinkedIn post scraper client for parallel batch runs."""
from typing import Any, Dict, List, Optional

from app.config import apify_client, logger
from app.utils.helpers import normalize_linkedin_url
from app.services.user_observation_fetch_service.config import UserObservationFetchConfig
from .interface import PostScraperClientInterface


def _normalize_urls(urls: List[str]) -> List[str]:
    out = []
    for u in urls:
        u = (u or "").strip()
        if not u.startswith("http://") and not u.startswith("https://"):
            u = "https://" + u
        if u.startswith("https://linkedin.com") and not u.startswith("https://www.linkedin.com"):
            u = u.replace("https://linkedin.com", "https://www.linkedin.com")
        elif u.startswith("http://linkedin.com") and not u.startswith("http://www.linkedin.com"):
            u = u.replace("http://linkedin.com", "https://www.linkedin.com")
        out.append(u)
    return out


class ApifyPostScraperClient(PostScraperClientInterface):
    """Uses Apify LinkedIn post scraper for batch runs."""

    def __init__(self, config: Optional[UserObservationFetchConfig] = None):
        self.config = config or UserObservationFetchConfig()

    def is_configured(self) -> bool:
        return apify_client is not None

    async def start_batch_run(
        self,
        urls: List[str],
        cookies_dict: List[dict],
        user_agent: str,
        max_posts: int,
    ) -> Dict[str, Any]:
        """Start one batch run. Returns dict with batch_id, apify_run_id, status, urls_count, error."""
        apify_urls = _normalize_urls(urls)
        run_input = {
            "urls": apify_urls,
            "limitPerSource": max_posts,
            "cookie": cookies_dict,
            "userAgent": user_agent,
            "proxy": {"useApifyProxy": True, "apifyProxyGroups": []},
        }
        try:
            run = apify_client.actor(self.config.post_scraper_actor_id).start(run_input=run_input)
            run_data = run.get("data", run) if isinstance(run, dict) else {}
            apify_run_id = run_data.get("id")
            if not apify_run_id:
                raise Exception("Apify start did not return a run id")
            return {
                "apify_run_id": apify_run_id,
                "status": "RUNNING",
                "urls_count": len(urls),
                "error": None,
            }
        except Exception as e:
            return {
                "apify_run_id": None,
                "status": "FAILED",
                "urls_count": len(urls),
                "error": str(e),
            }

    def get_run_status(self, run_id: str) -> Dict[str, Any]:
        """Get run status; returns dict with status, defaultDatasetId, statusMessage."""
        if not apify_client:
            return {"status": "UNKNOWN", "error": "Apify client not initialized"}
        try:
            run = apify_client.run(run_id).get()
            run_data = run.get("data", run) if isinstance(run, dict) else run
            if not isinstance(run_data, dict):
                return {"status": "UNKNOWN", "error": "Unexpected response"}
            return {
                "status": run_data.get("status"),
                "defaultDatasetId": run_data.get("defaultDatasetId"),
                "statusMessage": run_data.get("statusMessage"),
            }
        except Exception as e:
            logger.warning(f"Error getting run status for {run_id}: {e}")
            return {"status": "UNKNOWN", "error": str(e)}
