"""Handler for User Observation Fetch service: Trigger Parallel Scraping and Get Status."""
import asyncio
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from app.config import logger
from app.utils.helpers import ensure_db_available, normalize_linkedin_url
from app.services.user_observation_fetch_service.config import UserObservationFetchConfig
from app.services.user_observation_fetch_service.clients.scraping.factory import get_post_scraper_client
from app.services.user_observation_fetch_service.utils import split_urls_into_batches
from app.services.user_observation_fetch_service.repositories import create_job, update_job
from app.services.user_observation_fetch_service.status.parallel_scrape_status_handler import (
    ParallelScrapeStatusHandler,
)


class UserObservationFetchHandler:
    """Triggers parallel post scraping and provides status polling."""

    def __init__(self):
        self.config = UserObservationFetchConfig()
        self.scraper_client = get_post_scraper_client(self.config)
        self.status_handler = ParallelScrapeStatusHandler()

    def _validate(self) -> None:
        ensure_db_available("audience")
        if not self.scraper_client.is_configured():
            raise HTTPException(
                status_code=503,
                detail="Apify client not initialized. Please set APIFY_API_TOKEN.",
            )

    async def trigger_parallel_scraping(
        self,
        linkedin_urls: List[str],
        cookies: List[Any],
        user_agent: str,
        max_posts: int,
        audience_room_id: Optional[str] = None,
        enterprise_name: Optional[str] = None,
        batch_size: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Normalize URLs, split into batches, create scrape job, start batches in parallel (max 3),
        store batch info in job result. Returns job_id, status, total_urls, total_batches, etc.
        """
        self._validate()
        cookies_dict = [c.model_dump(exclude_none=True) if hasattr(c, "model_dump") else c for c in cookies]
        normalized_urls = [u for u in (normalize_linkedin_url(u) for u in linkedin_urls) if u]
        batch_size = min(batch_size or self.config.max_urls_per_batch, self.config.max_urls_per_batch)
        batches = split_urls_into_batches(linkedin_urls, batch_size)

        job_id = str(uuid.uuid4())
        try:
            job = create_job(
                linkedin_urls=normalized_urls,
                max_posts=max_posts,
                audience_room_id=audience_room_id,
                enterprise_name=enterprise_name,
            )
            job_id = job.id
            logger.info(f"Created scrape job {job_id} for parallel scraping")
        except Exception as e:
            logger.warning(f"Failed to create scrape job in database: {e}")

        semaphore = asyncio.Semaphore(self.config.max_concurrent_batches)

        async def run_one_batch(batch_idx: int, batch_urls: List[str]) -> Dict[str, Any]:
            async with semaphore:
                result = await self.scraper_client.start_batch_run(
                    urls=batch_urls,
                    cookies_dict=cookies_dict,
                    user_agent=user_agent,
                    max_posts=max_posts,
                )
                result["batch_id"] = f"{job_id}_batch_{batch_idx}"
                return result

        tasks = [run_one_batch(i, batch_urls) for i, batch_urls in enumerate(batches)]
        batch_results = await asyncio.gather(*tasks)

        apify_run_ids = [b.get("apify_run_id") for b in batch_results if b.get("apify_run_id")]
        try:
            update_job(
                job_id,
                {
                    "status": "PROCESSING",
                    "apifyRunId": apify_run_ids[0] if apify_run_ids else None,
                    "result": {
                        "parallel_mode": True,
                        "total_urls": len(linkedin_urls),
                        "total_batches": len(batches),
                        "completed_batches": 0,
                        "failed_batches": sum(1 for b in batch_results if b.get("status") == "FAILED"),
                        "batches": batch_results,
                        "batch_run_ids": apify_run_ids,
                        "audience_room_id": audience_room_id,
                        "linkedin_urls": normalized_urls,
                        "created_at": datetime.utcnow().isoformat(),
                        "updated_at": datetime.utcnow().isoformat(),
                    },
                },
                enterprise_name=enterprise_name,
            )
            logger.info(f"Stored parallel scrape job {job_id} in database with {len(batches)} batches")
        except Exception as e:
            logger.error(f"Failed to store scrape job in database: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to store job: {e}")

        running_count = sum(1 for b in batch_results if b.get("status") == "RUNNING")
        failed_count = sum(1 for b in batch_results if b.get("status") == "FAILED")
        return {
            "job_id": job_id,
            "status": "PROCESSING",
            "total_urls": len(linkedin_urls),
            "total_batches": len(batches),
            "batches_started": running_count,
            "batches_failed": failed_count,
            "message": f"Parallel scraping started with {len(batches)} batches. Use /api/v1/scrape/parallel/status/{job_id} to check progress.",
        }

    async def get_job_status(
        self,
        job_id: str,
        enterprise_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get parallel scrape job status (poll batches, process posts when done)."""
        response = await self.status_handler.check_job_status(job_id, enterprise_name)
        if response.get("_not_found"):
            raise HTTPException(status_code=404, detail=f"Parallel scrape job {job_id} not found")
        if response.get("_bad_request"):
            raise HTTPException(status_code=400, detail=response.get("message", "Not a parallel scrape job"))
        return response
