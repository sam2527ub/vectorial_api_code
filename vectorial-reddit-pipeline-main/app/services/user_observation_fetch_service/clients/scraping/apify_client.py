"""Apify implementation of ScrapingClientInterface."""
import asyncio
import os
import random
from typing import Optional, Any, Dict, List
from apify_client import ApifyClient as ApifySDKClient
from fastapi import HTTPException
from app.config import logger
from app.services.user_observation_fetch_service.config import ScrapingConfig
from .interface import ScrapingClientInterface
from .apify_utils import (
    start_apify_run,
    fetch_activities_from_dataset
)


class ApifyScrapingClient(ScrapingClientInterface):
    """Apify implementation of ScrapingClientInterface."""

    def __init__(self, config: Optional[ScrapingConfig] = None):
        """Initialize Apify client."""
        self.config = config or ScrapingConfig()
        self._client: Optional[ApifySDKClient] = None
        self._initialize()

    def _initialize(self) -> None:
        """Initialize Apify SDK client."""
        try:
            apify_token = os.getenv("APIFY_API_TOKEN")
            if apify_token:
                self._client = ApifySDKClient(apify_token)
                logger.info("Apify client initialized successfully for user observation fetch")
            else:
                logger.warning("APIFY_API_TOKEN not set")
                self._client = None
        except Exception as e:
            logger.error(f"Failed to initialize Apify client: {e}")
            self._client = None

    def is_configured(self) -> bool:
        """Return True if the client is properly configured."""
        return self._client is not None

    def get_raw_client(self) -> Any:
        """Return the underlying SDK client."""
        return self._client

    async def start_scraping_job(
        self,
        username_batch: List[str],
        include_comments: bool,
        max_items: int,
        sort: str,
        time: str
    ) -> str:
        """Start scraping job with retry logic. Returns job identifier."""
        for attempt in range(self.config.max_retries):
            try:
                return start_apify_run(
                    self._client,
                    self.config.scraper_actor_id,
                    username_batch,
                    include_comments,
                    max_items,
                    sort,
                    time
                )
            except Exception as e:
                if attempt < self.config.max_retries - 1:
                    delay = (2 ** attempt) * self.config.initial_retry_delay + random.uniform(0, 2)
                    logger.warning(
                        f"Attempt {attempt + 1}/{self.config.max_retries} failed, "
                        f"retrying in {delay:.1f}s: {e}"
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"All {self.config.max_retries} attempts failed: {e}")
                    raise HTTPException(
                        status_code=500,
                        detail=f"Failed after {self.config.max_retries} attempts: {str(e)}"
                    )

    def get_job_status(self, job_id: str) -> Dict[str, Any]:
        """Get status of a scraping job."""
        if not self._client:
            raise HTTPException(
                status_code=503,
                detail="Scraping client not configured"
            )
        return self._client.run(job_id).get()

    def fetch_job_results(self, job_id: str) -> List[Dict[str, Any]]:
        """Fetch results from a completed job."""
        if not self._client:
            raise HTTPException(
                status_code=503,
                detail="Scraping client not configured"
            )
        
        run_status = self._client.run(job_id).get()
        dataset_id = run_status.get("defaultDatasetId")
        
        if not dataset_id:
            return []
        
        return fetch_activities_from_dataset(self._client, dataset_id)
