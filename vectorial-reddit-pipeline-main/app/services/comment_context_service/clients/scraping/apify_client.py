"""Apify implementation of PostScrapingClientInterface."""
import asyncio
import os
import random
from typing import Optional, Any, Dict, List
from apify_client import ApifyClient as ApifySDKClient
from fastapi import HTTPException
from app.config import logger
from app.services.comment_context_service.config import CommentContextServiceConfig
from .interface import PostScrapingClientInterface
from .apify_utils import start_post_scraping_run, fetch_activities_from_dataset


class ApifyPostScrapingClient(PostScrapingClientInterface):
    """Apify implementation for scraping post URLs with comments."""

    def __init__(self, config: Optional[CommentContextServiceConfig] = None):
        self.config = config or CommentContextServiceConfig()
        self._client: Optional[ApifySDKClient] = None
        self._initialize()

    def _initialize(self) -> None:
        try:
            apify_token = os.getenv("APIFY_API_TOKEN")
            if apify_token:
                self._client = ApifySDKClient(apify_token)
                logger.info("Apify client initialized successfully for comment context service")
            else:
                logger.warning("APIFY_API_TOKEN not set")
                self._client = None
        except Exception as e:
            logger.error(f"Failed to initialize Apify client: {e}")
            self._client = None

    def is_configured(self) -> bool:
        return self._client is not None

    def get_raw_client(self) -> Any:
        return self._client

    async def start_post_scraping_job(self, post_urls: List[str]) -> str:
        """Start scraping job with retry logic. Returns job identifier."""
        max_retries = 3
        initial_retry_delay = 10
        
        for attempt in range(max_retries):
            try:
                return start_post_scraping_run(
                    self._client,
                    self.config.scraper_actor_id,
                    post_urls
                )
            except Exception as e:
                if attempt < max_retries - 1:
                    delay = (2 ** attempt) * initial_retry_delay + random.uniform(0, 2)
                    logger.warning(
                        f"Attempt {attempt + 1}/{max_retries} failed, retrying in {delay:.1f}s: {e}"
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"All {max_retries} attempts failed: {e}")
                    raise HTTPException(
                        status_code=500,
                        detail=f"Failed after {max_retries} attempts: {str(e)}"
                    )

    def get_job_status(self, job_id: str) -> Dict[str, Any]:
        """Get status of a scraping job."""
        if not self._client:
            raise HTTPException(status_code=503, detail="Scraping client not configured")
        return self._client.run(job_id).get()

    def fetch_job_results(self, job_id: str) -> List[Dict[str, Any]]:
        """Fetch results from a completed job."""
        if not self._client:
            raise HTTPException(status_code=503, detail="Scraping client not configured")
        
        run_status = self._client.run(job_id).get()
        dataset_id = run_status.get("defaultDatasetId")
        
        if not dataset_id:
            return []
        
        return fetch_activities_from_dataset(self._client, dataset_id)
