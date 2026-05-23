"""Request handler for user activity scraping."""
import asyncio
import random
from datetime import datetime
from typing import Dict, Any, List
from fastapi import HTTPException
from app.config import logger
from app.services.user_observation_fetch_service.config import ScrapingConfig
from app.services.user_observation_fetch_service.utils.username_utils import UsernameProcessor
from app.services.user_observation_fetch_service.clients.scraping.factory import get_scraping_client
from app.services.user_observation_fetch_service.schemas import UserActivityScrapeRequest


class UserActivityScrapingHandler:
    """Handles user activity scraping requests."""
    
    def __init__(self):
        """Initialize request handler."""
        self.config = ScrapingConfig()
        self.username_processor = UsernameProcessor()
        self.scraping_client = get_scraping_client(self.config)
    
    def _validate_request(self, payload: UserActivityScrapeRequest) -> None:
        """Validate scraping request."""
        if not self.scraping_client.is_configured():
            raise HTTPException(
                status_code=503,
                detail="Scraping client not configured. Please set APIFY_API_TOKEN environment variable."
            )
        
        if not payload.usernames:
            raise HTTPException(status_code=400, detail="At least one username is required")
    
    def _process_usernames(self, usernames: List[str]) -> List[str]:
        """Normalize and deduplicate usernames."""
        normalized = self.username_processor.normalize_usernames(usernames)
        unique = self.username_processor.remove_duplicates(normalized)
        
        if not unique:
            raise HTTPException(status_code=400, detail="No valid usernames provided")
        
        return unique
    
    async def _start_batches(
        self,
        batches: List[List[str]],
        include_comments: bool,
        max_items: int,
        sort: str,
        time: str
    ) -> tuple[List[str], List[Dict[str, Any]]]:
        """Start scraping jobs for all batches."""
        run_ids = []
        batch_info = []
        
        for i, batch in enumerate(batches):
            try:
                # Add delay between batches
                if i > 0:
                    delay = self.config.batch_start_delay + random.uniform(0, self.config.batch_delay_jitter)
                    logger.info(f"Waiting {delay:.1f}s before batch {i+1}/{len(batches)}")
                    await asyncio.sleep(delay)
                
                run_id = await self.scraping_client.start_scraping_job(
                    username_batch=batch,
                    include_comments=include_comments,
                    max_items=max_items,
                    sort=sort,
                    time=time
                )
                
                run_ids.append(run_id)
                batch_info.append({
                    "batch_index": i,
                    "run_id": run_id,
                    "usernames": batch,
                    "user_count": len(batch)
                })
                logger.info(f"Started batch {i+1}/{len(batches)}: run_id={run_id}, users={len(batch)}")
                
            except Exception as e:
                logger.error(f"Error starting batch {i+1}: {e}", exc_info=True)
                batch_info.append({
                    "batch_index": i,
                    "run_id": None,
                    "usernames": batch,
                    "user_count": len(batch),
                    "error": str(e)
                })
        
        return run_ids, batch_info
    
    async def handle_request(self, payload: UserActivityScrapeRequest) -> Dict[str, Any]:
        """Handle scraping request."""
        # Validate request
        self._validate_request(payload)
        
        # Process usernames
        unique_usernames = self._process_usernames(payload.usernames)
        logger.info(f"Starting scrape for {len(unique_usernames)} unique usernames")
        
        # Split into batches
        batches = self.username_processor.split_into_batches(
            unique_usernames, self.config.batch_size
        )
        logger.info(f"Split into {len(batches)} batches of ~{self.config.batch_size} users")
        
        # Start batches
        run_ids, batch_info = await self._start_batches(
            batches=batches,
            include_comments=payload.include_comments,
            max_items=payload.max_items,
            sort=payload.sort,
            time=payload.time
        )
        
        if not run_ids:
            raise HTTPException(status_code=500, detail="Failed to start any scraping jobs")
        
        batch_id = run_ids[0]
        
        response = {
            "batch_id": batch_id,
            "run_ids": run_ids,
            "status": "started",
            "total_users": len(unique_usernames),
            "total_batches": len(batches),
            "batches": batch_info,
            "scraper": "epctex/reddit-scraper",
            "check_status_url": "/api/v1/user-activity/status/batch",
            "started_at": datetime.now().isoformat(),
            "message": (
                f"Scraping started with {len(run_ids)} batches. "
                f"Use POST /api/v1/user-activity/status/batch with run_ids to poll for results."
            )
        }
        
        return response
