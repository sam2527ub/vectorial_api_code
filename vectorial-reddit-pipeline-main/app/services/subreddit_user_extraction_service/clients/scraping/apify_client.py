"""Apify implementation of ScrapingClientInterface."""
import asyncio
import os
from typing import Optional, Any, Dict
from apify_client import ApifyClient as ApifySDKClient
from fastapi import HTTPException
from app.config import logger
from app.services.subreddit_user_extraction_service.config import ExtractionConfig
from .interface import ScrapingClientInterface
from .apify_dataset import (
    get_item_count,
    fetch_users_from_dataset,
    should_abort_for_max_users,
    build_result_dict,
)
from .apify_run import (
    prepare_run_input,
    start_apify_run,
    handle_succeeded_status,
    handle_failed_status,
    fetch_partial_results_on_timeout
)


class ApifyScrapingClient(ScrapingClientInterface):
    """Apify implementation of ScrapingClientInterface."""
    
    def __init__(self, config: Optional[ExtractionConfig] = None):
        """Initialize Apify client."""
        self.config = config or ExtractionConfig()
        self._client: Optional[ApifySDKClient] = None
        self._initialize()
    
    def _initialize(self) -> None:
        """Initialize Apify SDK client."""
        try:
            apify_token = os.getenv("APIFY_API_TOKEN")
            if apify_token:
                self._client = ApifySDKClient(apify_token)
                logger.info("Apify client initialized successfully for subreddit user extraction")
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
    
    async def _monitor_and_abort_if_needed(
        self,
        run_id: str,
        subreddit: str,
        dataset_id: str
    ) -> Optional[Dict[str, Any]]:
        """Monitor run progress and abort if limits are reached."""
        item_count = get_item_count(self._client, dataset_id, self.config.max_users_per_subreddit)
        
        if item_count > 0:
            if should_abort_for_max_users(item_count, self.config.max_users_per_subreddit):
                logger.warning(
                    f"r/{subreddit}: Max users per subreddit reached ({item_count}), aborting"
                )

                try:
                    self._client.run(run_id).abort()
                except Exception as abort_error:
                    logger.warning(f"Failed to abort run {run_id}: {abort_error}")

                await asyncio.sleep(2)
                users = fetch_users_from_dataset(self._client, dataset_id)

                return build_result_dict(
                    subreddit=subreddit,
                    status="partial",
                    users=users,
                    run_id=run_id,
                    aborted=True,
                    abort_reason=f"Max users per subreddit ({self.config.max_users_per_subreddit}) reached at {item_count} users",
                )
        
        return None
    
    async def _poll_run_status(
        self,
        run_id: str,
        subreddit: str
    ) -> Dict[str, Any]:
        """Poll run status until completion or timeout."""
        elapsed = 0
        
        while elapsed < self.config.max_wait_time:
            await asyncio.sleep(self.config.wait_interval)
            elapsed += self.config.wait_interval
            
            if not self._client:
                continue
            
            try:
                run_status_response = self._client.run(run_id).get()
                status = run_status_response.get("status", "").upper()
                dataset_id = run_status_response.get("defaultDatasetId")
                
                if dataset_id and status in ["RUNNING", "READY"]:
                    abort_result = await self._monitor_and_abort_if_needed(
                        run_id,
                        subreddit,
                        dataset_id
                    )
                    if abort_result:
                        return abort_result
                
                if status == "SUCCEEDED":
                    return handle_succeeded_status(
                        self._client, subreddit, run_id, dataset_id
                    )
                
                elif status in ["FAILED", "ABORTED", "TIMED-OUT"]:
                    error_msg = run_status_response.get("statusMessage", "Unknown error")
                    return handle_failed_status(
                        self._client, subreddit, run_id, dataset_id, error_msg
                    )
                
                elif status in ["RUNNING", "READY"]:
                    continue
                    
            except Exception as status_error:
                logger.error(f"Error checking status for r/{subreddit}: {status_error}")
                continue
        
        logger.warning(f"Apify run timed out for r/{subreddit} after {self.config.max_wait_time} seconds")
        users = fetch_partial_results_on_timeout(self._client, run_id)
        return build_result_dict(
            subreddit, "error", users, error="Scraping timed out (exceeded 1 hour)"
        )
    
    async def scrape_subreddit(
        self,
        subreddit: str,
        fetch_details: bool,
        max_posts: Optional[int] = None,
        max_comments: Optional[int] = None
    ) -> Dict[str, Any]:
        """Scrape a single subreddit and return results."""
        try:
            run_input = prepare_run_input(subreddit, fetch_details, max_posts, max_comments)
            run_id = start_apify_run(
                self._client,
                self.config.scraper_actor_id,
                subreddit,
                run_input
            )
            return await self._poll_run_status(run_id, subreddit)
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error scraping r/{subreddit}: {e}", exc_info=True)
            return build_result_dict(subreddit, "error", [], error=str(e))
