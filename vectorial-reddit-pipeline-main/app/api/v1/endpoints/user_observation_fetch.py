"""Reddit user activity scraping endpoints."""
from typing import List
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.config import logger
from app.services.user_observation_fetch_service import request_handler, status_handler
from app.services.user_observation_fetch_service.schemas import UserActivityScrapeRequest

router = APIRouter()


# Request/Response Schemas
class BatchStatusRequest(BaseModel):
    """Request schema for checking batch status with run_ids."""
    run_ids: List[str] = Field(..., min_items=1, description="List of Apify run IDs to check")


@router.post("/api/v1/user-activity/scrape")
async def scrape_user_activities(payload: UserActivityScrapeRequest):
    """Scrape Reddit user activities and return run_ids for polling."""
    # ===== REQUEST RECEIVED =====
    logger.info("=" * 80)
    logger.info("REQUEST RECEIVED: POST /api/v1/user-activity/scrape")
    logger.info(f"Request Details:")
    logger.info(f"  - Usernames Count: {len(payload.usernames)}")
    logger.info(f"  - Include Comments: {payload.include_comments}")
    logger.info(f"  - Max Items: {payload.max_items}")
    logger.info(f"  - Sort: {payload.sort}")
    logger.info(f"  - Time: {payload.time}")
    logger.info("=" * 80)
    
    try:
        response = await request_handler.handle_request(payload)
        
        logger.info("RESPONSE SENT: POST /api/v1/user-activity/scrape")
        logger.info(f"Response Details:")
        logger.info(f"  - Batch ID: {response['batch_id']}")
        logger.info(f"  - Status: {response['status']}")
        logger.info(f"  - Total Users: {response['total_users']}")
        logger.info(f"  - Total Batches: {response['total_batches']}")
        logger.info(f"  - Run IDs Count: {len(response['run_ids'])}")
        logger.info(f"  - Started At: {response['started_at']}")
        logger.info("=" * 80)
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting user activity scrape: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@router.post("/api/v1/user-activity/status/batch")
async def get_batch_status(payload: BatchStatusRequest):
    """Poll for scrape results by run_ids and return aggregated activities."""
    logger.info("=" * 80)
    logger.info("REQUEST RECEIVED: POST /api/v1/user-activity/status/batch")
    logger.info(f"Request Details:")
    logger.info(f"  - Run IDs Count: {len(payload.run_ids)}")
    logger.info(f"  - Run IDs: {payload.run_ids}")
    logger.info("=" * 80)
    
    if not payload.run_ids:
        raise HTTPException(status_code=400, detail="At least one run_id is required")
    
    try:
        response = await status_handler.check_batch_status(payload.run_ids)
        
        logger.info("RESPONSE SENT: POST /api/v1/user-activity/status/batch")
        logger.info(f"Response Details:")
        logger.info(f"  - Status: {response['status']}")
        logger.info(f"  - Total Runs: {response['total_runs']}")
        logger.info(f"  - Successful: {response['successful_runs']}, Failed: {response['failed_runs']}, Running: {response.get('running_runs', 0)}")
        if 'total_users' in response:
            logger.info(f"  - Total Users: {response['total_users']}")
            logger.info(f"  - Successful Users: {response['successful_users']}, Failed Users: {response['failed_users']}")
            logger.info(f"  - Total Activities: {response['total_activities']}")
        if 'activities_s3_url' in response:
            logger.info(f"  - Activities S3 URL: {response['activities_s3_url']}")
        logger.info(f"  - Checked At: {response['checked_at']}")
        logger.info("=" * 80)
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error checking batch status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@router.get("/api/v1/user-activity/status/{run_id}")
async def get_single_run_status(run_id: str):
    """Get status of a single Apify run."""
    logger.info("=" * 80)
    logger.info("REQUEST RECEIVED: GET /api/v1/user-activity/status/{run_id}")
    logger.info(f"Request Details:")
    logger.info(f"  - Run ID: {run_id}")
    logger.info("=" * 80)
    
    try:
        result = await status_handler.check_single_run_status(run_id)
        
        logger.info("RESPONSE SENT: GET /api/v1/user-activity/status/{run_id}")
        logger.info(f"Response Details:")
        logger.info(f"  - Run ID: {result['run_id']}")
        logger.info(f"  - Status: {result['status']}")
        if "activities_count" in result:
            logger.info(f"  - Activities Count: {result['activities_count']}")
        if "error" in result:
            logger.info(f"  - Error: {result['error']}")
        logger.info(f"  - Checked At: {result['checked_at']}")
        logger.info("=" * 80)
        
        return result
        
    except Exception as e:
        logger.error(f"Error checking run status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")
