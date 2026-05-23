"""Request handler for subreddit user extraction."""
import asyncio
from typing import Dict, Any
from fastapi import HTTPException
from app.config import logger
from app.services.subreddit_user_extraction_service.clients.scraping.factory import get_scraping_client
from app.services.subreddit_user_extraction_service.clients.scraping.interface import ScrapingClientInterface
from app.services.subreddit_user_extraction_service.config import ExtractionConfig
from app.services.subreddit_user_extraction_service.schemas import SubredditBatchExtractionRequest
from app.services.subreddit_user_extraction_service.utils.subreddit_processor import SubredditProcessor
from app.services.subreddit_user_extraction_service.utils.result_processor import ResultProcessor


class SubredditUserExtractionHandler:
    """Handles subreddit user extraction requests."""
    
    def __init__(self):
        """Initialize request handler."""
        self.config = ExtractionConfig()
        self.subreddit_processor = SubredditProcessor()
        self.scraping_client = get_scraping_client(self.config)
        self.result_processor = ResultProcessor(self.config)
    
    def _validate_request(
        self,
        payload: SubredditBatchExtractionRequest,
        scraping_client: ScrapingClientInterface,
    ) -> None:
        """Validate extraction request."""
        if not scraping_client.is_configured():
            raise HTTPException(
                status_code=503,
                detail="Scraping client not configured. Please set APIFY_API_TOKEN environment variable."
            )
    
    async def handle_request(
        self,
        payload: SubredditBatchExtractionRequest
    ) -> Dict[str, Any]:
        """Handle batch subreddit user extraction request."""
        # Per-request config with optional max_users override
        config = ExtractionConfig()
        if payload.max_users_per_subreddit is not None:
            config.max_users_per_subreddit = payload.max_users_per_subreddit

        scraping_client = get_scraping_client(config)
        result_processor = ResultProcessor(config)

        self._validate_request(payload, scraping_client)
        
        # Normalize subreddit names
        normalized_subreddits = self.subreddit_processor.normalize_subreddits(
            payload.subreddits
        )
        
        if not normalized_subreddits:
            raise HTTPException(
                status_code=400,
                detail="At least one valid subreddit is required"
            )
        
        logger.info(
            f"Starting parallel extraction for {len(normalized_subreddits)} subreddits: "
            f"{normalized_subreddits}"
        )
        
        # Scrape all subreddits in parallel using the generic interface
        results = await asyncio.gather(*[
            scraping_client.scrape_subreddit(
                subreddit=subreddit,
                fetch_details=payload.fetch_details,
                max_posts=payload.max_posts,
                max_comments=payload.max_comments
            )
            for subreddit in normalized_subreddits
        ])
        
        # Process and aggregate results
        response = result_processor.process_results(results)
        
        return response
