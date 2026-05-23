"""Subreddit user extraction endpoints."""
from fastapi import APIRouter, HTTPException
from app.api.schemas import RedditBatchScrapeRequest
from app.config import logger
from app.services.subreddit_user_extraction_service import request_handler
from app.services.subreddit_user_extraction_service.schemas import SubredditBatchExtractionRequest

router = APIRouter()


@router.post("/api/v1/scrape/reddit/batch")
async def scrape_reddit_subreddits_batch(payload: RedditBatchScrapeRequest):
    """Scrape multiple subreddits in parallel and return aggregated results."""
    logger.info("=" * 80)
    logger.info("REQUEST RECEIVED: POST /api/v1/scrape/reddit/batch")
    logger.info(f"Request Details:")
    logger.info(f"  - Subreddits Count: {len(payload.subreddits)}")
    logger.info(f"  - Subreddits: {payload.subreddits}")
    logger.info(f"  - Fetch Details: {payload.fetch_details}")
    logger.info(f"  - Max Posts: {payload.max_posts}")
    logger.info(f"  - Max Comments: {payload.max_comments}")
    logger.info(f"  - Max Users Per Subreddit: {payload.max_users_per_subreddit}")
    logger.info("=" * 80)
    
    try:
        service_payload = SubredditBatchExtractionRequest(
            subreddits=payload.subreddits,
            max_posts=payload.max_posts,
            max_comments=payload.max_comments,
            fetch_details=payload.fetch_details,
            max_users_per_subreddit=payload.max_users_per_subreddit,
        )
        
        response = await request_handler.handle_request(service_payload)
        
        logger.info("RESPONSE SENT: POST /api/v1/scrape/reddit/batch")
        logger.info(f"Response Details:")
        logger.info(f"  - Total Subreddits: {response['total_subreddits']}")
        logger.info(f"  - Successful: {response['successful_subreddits']}, Failed: {response['failed_subreddits']}")
        logger.info(f"  - Total Users (before filter): {response['total_users']}")
        logger.info(f"  - Filtered Users (contribution >= 2): {response['filtered_users_count']}")
        logger.info(f"  - Completed At: {response['completed_at']}")
        logger.info("=" * 80)
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in scrape_reddit_subreddits_batch: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to scrape subreddits: {str(e)}")

