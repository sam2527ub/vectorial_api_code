"""Request handler for web indexing service (parallel search for people/LinkedIn). No DB - poll API directly."""
from typing import Any, Dict, Optional

from fastapi import HTTPException

from app.config import logger
from app.services.web_indexing_service.config import WebIndexingConfig
from app.services.web_indexing_service.clients.search.factory import get_search_client
from app.services.web_indexing_service.clients.search.parallel_utils import extract_findall_id
from app.services.web_indexing_service.status.web_indexing_status_handler import WebIndexingStatusHandler


class WebIndexingHandler:
    """Handles web indexing (parallel search) requests. job_id is Parallel API findall_id; no database."""

    def __init__(self):
        self.config = WebIndexingConfig()
        self.api_client = get_search_client(self.config)
        self.status_handler = WebIndexingStatusHandler(self.config)

    def _validate_config(self) -> None:
        if not self.api_client.is_configured():
            logger.error("PARALLEL_API_KEY not configured")
            raise HTTPException(
                status_code=503,
                detail="PARALLEL_API_KEY not configured. Please set the environment variable.",
            )

    async def start_async_job(
        self,
        query: str,
        model: str,
        match_limit: int,
        enterprise_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Start a Parallel run (people). Return job_id = findall_id for direct status polling. No DB."""
        self._validate_config()
        try:
            response = await self.api_client.start_scraping_job(
                query=query,
                model=model,
                match_limit=match_limit,
                entity_type=self.config.default_entity_type,
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error starting Parallel run: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=f"Failed to start Parallel run: {str(e)}",
            )
        findall_id = response.get("findall_id") or extract_findall_id(response.get("data", {}))
        logger.info(f"Parallel run started: job_id={findall_id}")
        return {
            "job_id": findall_id,
            "status": "PENDING",
            "message": "Parallel search job started. Use /api/search/parallel/status/{job_id} to check progress.",
        }

    async def get_job_status(
        self,
        job_id: str,
        enterprise_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get job status by polling Parallel API directly. job_id is findall_id. No DB."""
        response = await self.status_handler.check_job_status(job_id, enterprise_name)
        if response.get("status") == "not_found":
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
        return response
