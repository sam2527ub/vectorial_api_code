"""Apify scraping utilities."""
from typing import Dict, Any, List
from fastapi import HTTPException
from app.config import logger
from app.services.user_observation_fetch_service.utils.username_utils import UsernameProcessor


def prepare_run_input(
    username_batch: List[str],
    include_comments: bool,
    max_items: int,
    sort: str,
    time: str
) -> Dict[str, Any]:
    """Prepare run input for Apify actor."""
    username_processor = UsernameProcessor()
    start_urls = username_processor.convert_usernames_to_urls(username_batch)
    
    return {
        "startUrls": start_urls,
        "includeComments": include_comments,
        "maxItems": max_items,
        "sort": sort,
        "time": time,
        "searchMode": "link",
        "proxy": {
            "useApifyProxy": True,
            "apifyProxyGroups": ["RESIDENTIAL"]
        }
    }


def start_apify_run(
    apify_client,
    scraper_actor_id: str,
    username_batch: List[str],
    include_comments: bool,
    max_items: int,
    sort: str,
    time: str
) -> str:
    """Start Apify run. Returns run ID."""
    if not apify_client:
        raise HTTPException(
            status_code=503,
            detail="Scraping client not configured. Please set APIFY_API_TOKEN environment variable."
        )
    
    run_input = prepare_run_input(
        username_batch, include_comments, max_items, sort, time
    )
    
    logger.info(f"Starting epctex/reddit-scraper for {len(run_input['startUrls'])} users")
    
    try:
        run = apify_client.actor(scraper_actor_id).start(run_input=run_input)
        run_data = run.get("data", run) if isinstance(run, dict) else None
        
        if not run_data or not isinstance(run_data, dict):
            raise HTTPException(
                status_code=500,
                detail="Unexpected response from Apify API"
            )
        
        run_id = run_data.get('id')
        if not run_id:
            raise HTTPException(
                status_code=500,
                detail="Failed to get run ID from Apify API"
            )
        
        logger.info(f"Started Apify run: {run_id}")
        return run_id
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting Apify run: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to start Apify run: {str(e)}"
        )


def fetch_activities_from_dataset(apify_client, dataset_id: str) -> List[Dict[str, Any]]:
    """Fetch activities from Apify dataset."""
    activities = []
    if not apify_client:
        return activities
    
    try:
        dataset_client = apify_client.dataset(dataset_id)
        items_response = dataset_client.list_items()
        items = items_response.items if hasattr(items_response, 'items') else []
        
        for item in items:
            if isinstance(item, dict):
                activities.append(item)
    except Exception as e:
        logger.error(f"Error fetching activities from dataset {dataset_id}: {e}")
    
    return activities
