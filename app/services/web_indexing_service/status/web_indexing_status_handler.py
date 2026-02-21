"""Status handler for web indexing (parallel / Apify) runs. Polls provider API - no database."""
import httpx
from typing import Any, Dict, Optional

from app.config import logger
from app.services.web_indexing_service.config import WebIndexingConfig
from app.services.web_indexing_service.clients.search.factory import get_search_client, Provider
from app.services.web_indexing_service.clients.search.parallel_utils import extract_run_status


class WebIndexingStatusHandler:
    """Handles status checking and result fetching by polling the provider API (Parallel or Apify)."""

    def __init__(self, config: Optional[WebIndexingConfig] = None):
        self.config = config or WebIndexingConfig()

    async def check_job_status(
        self,
        job_id: str,
        enterprise_name: Optional[str] = None,
        provider: Provider = "parallel",
    ) -> Dict[str, Any]:
        """
        Check run status from the given provider. job_id is findall_id (Parallel) or run_id (Apify).
        If completed, fetch profiles and return; otherwise return status from API.
        """
        api_client = get_search_client(provider, self.config)
        try:
            api_response = await api_client.get_job_status(job_id)
        except httpx.TimeoutException:
            logger.error(f"Timeout checking {provider} status for job_id {job_id}")
            return {
                "job_id": job_id,
                "status": "processing",
                "message": f"Timeout checking {provider} API status",
            }
        except Exception as e:
            logger.error(f"Error checking {provider} status for job_id {job_id}: {e}", exc_info=True)
            return {
                "job_id": job_id,
                "status": "processing",
                "message": f"Error checking {provider} API status: {str(e)}",
            }

        if api_response.get("status") == "not_found":
            return {"job_id": job_id, "status": "not_found"}

        status_value = api_response.get("status", "")
        run_status = extract_run_status(api_response)

        if run_status in ("SUCCEEDED", "COMPLETED"):
            logger.info(f"Job run completed for job_id {job_id} ({provider}), fetching results")
            profiles = await api_client.fetch_job_results(job_id)
            query = _get_nested(api_response, "objective") or _get_nested(api_response, "query") or ""
            model = _get_nested(api_response, "generator") or _get_nested(api_response, "model") or ""
            return {
                "job_id": job_id,
                "status": "COMPLETED",
                "profiles": profiles,
                "total_found": len(profiles),
                "query": query,
                "model": model,
            }

        if run_status in ("FAILED", "ERROR", "ABORTED"):
            error_msg = api_response.get("error") or api_response.get("message") or "Unknown error"
            logger.error(f"Run failed for job_id {job_id} ({provider}): {error_msg}")
            return {
                "job_id": job_id,
                "status": "FAILED",
                "error": error_msg,
            }

        metrics = {}
        if isinstance(status_value, dict):
            metrics = status_value.get("metrics", {})
        return {
            "job_id": job_id,
            "status": "PROCESSING",
            "parallel_status": run_status,
            "metrics": metrics,
            "message": "Web indexing in progress...",
        }


def _get_nested(data: Dict[str, Any], key: str) -> Optional[str]:
    """Get key from data or from data.status if it's a dict."""
    val = data.get(key)
    if val is not None and isinstance(val, str):
        return val
    st = data.get("status")
    if isinstance(st, dict):
        val = st.get(key)
        if isinstance(val, str):
            return val
    return None
