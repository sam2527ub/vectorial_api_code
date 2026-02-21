"""Request handler for web indexing (parallel / Apify). No DB - poll provider API directly."""
from typing import Any, Dict, Optional

from fastapi import HTTPException

from app.config import logger
from app.services.web_indexing_service.config import WebIndexingConfig
from app.services.web_indexing_service.clients.search.factory import get_search_client, Provider
from app.services.web_indexing_service.clients.search.parallel_utils import extract_findall_id
from app.services.web_indexing_service.status.web_indexing_status_handler import WebIndexingStatusHandler


class WebIndexingHandler:
    """Handles web indexing requests (Parallel or Apify). job_id = findall_id or Apify run_id."""

    def __init__(self):
        self.config = WebIndexingConfig()
        self.status_handler = WebIndexingStatusHandler(self.config)

    def _validate_config(self, provider: Provider) -> None:
        client = get_search_client(provider, self.config)
        if not client.is_configured():
            if provider == "parallel":
                logger.error("PARALLEL_API_KEY not configured")
                raise HTTPException(
                    status_code=503,
                    detail="PARALLEL_API_KEY not configured. Please set the environment variable.",
                )
            logger.error("APIFY_TOKEN not configured")
            raise HTTPException(
                status_code=503,
                detail="APIFY_TOKEN not configured. Please set the environment variable.",
            )

    async def start_async_job(
        self,
        provider: Provider,
        params: Dict[str, Any],
        enterprise_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Start a web indexing job. provider: 'parallel' | 'apify'.
        params: for parallel use query, model, match_limit; for apify use companies, jobTitles, etc.
        Returns job_id for status polling.
        """
        self._validate_config(provider)
        client = get_search_client(provider, self.config)
        try:
            response = await client.start_scraping_job(params)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error starting {provider} run: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=f"Failed to start {provider} run: {str(e)}",
            )
        job_id = response.get("findall_id") or extract_findall_id(response.get("data", {}))
        status_url = f"/api/search/{provider}/status/{job_id}"
        logger.info(f"{provider} run started: job_id={job_id}")
        return {
            "job_id": job_id,
            "status": "PENDING",
            "message": f"Web indexing job started. Use {status_url} to check progress.",
        }

    async def get_job_status(
        self,
        provider: Provider,
        job_id: str,
        enterprise_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get job status by polling the provider API. job_id is findall_id (parallel) or run_id (apify)."""
        response = await self.status_handler.check_job_status(
            job_id, enterprise_name, provider=provider
        )
        if response.get("status") == "not_found":
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
        return response
