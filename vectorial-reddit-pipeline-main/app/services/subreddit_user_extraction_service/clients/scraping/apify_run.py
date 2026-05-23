"""Apify run management and status handling."""
from typing import Dict, Any, Optional, List
from fastapi import HTTPException
from app.config import logger
from .apify_dataset import fetch_users_from_dataset, build_result_dict


def prepare_run_input(
    subreddit: str,
    fetch_details: bool,
    max_posts: Optional[int],
    max_comments: Optional[int]
) -> Dict[str, Any]:
    """Prepare run input for Apify actor."""
    run_input = {
        "subreddit": subreddit,
        "fetchDetails": fetch_details,
    }
    if max_posts is not None:
        run_input["maxPosts"] = max_posts
    if max_comments is not None:
        run_input["maxComments"] = max_comments
    return run_input


def start_apify_run(
    apify_client,
    scraper_actor_id: str,
    subreddit: str,
    run_input: Dict[str, Any]
) -> str:
    """Start Apify run for subreddit scraping. Returns run ID."""
    if not apify_client:
        raise HTTPException(
            status_code=503,
            detail="Scraping client not configured. Please set APIFY_API_TOKEN environment variable."
        )
    try:
        run = apify_client.actor(scraper_actor_id).start(run_input=run_input)
        run_data = run.get("data", run) if isinstance(run, dict) else None
        
        if not run_data or not isinstance(run_data, dict):
            raise HTTPException(
                status_code=500,
                detail="Unexpected response format from Apify API"
            )
        
        run_id = run_data.get('id')
        if not run_id:
            raise HTTPException(
                status_code=500,
                detail="Failed to get run ID from Apify API"
            )
        
        logger.info(f"Apify run created: {run_id} for r/{subreddit}")
        return run_id
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting Apify run for r/{subreddit}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to start Apify run: {str(e)}"
        )


def handle_succeeded_status(
    apify_client,
    subreddit: str,
    run_id: str,
    dataset_id: Optional[str],
) -> Dict[str, Any]:
    """Handle SUCCEEDED status."""
    if not dataset_id:
        logger.warning(f"No dataset ID in Apify run for r/{subreddit}")
        return build_result_dict(
            subreddit, "error", [], error="Dataset not available after run completion"
        )

    users = fetch_users_from_dataset(apify_client, dataset_id)

    return build_result_dict(
        subreddit, "success", users, run_id=run_id
    )


def handle_failed_status(
    apify_client,
    subreddit: str,
    run_id: str,
    dataset_id: Optional[str],
    error_msg: str
) -> Dict[str, Any]:
    """Handle FAILED/ABORTED/TIMED-OUT status."""
    logger.error(f"Apify run failed for r/{subreddit}: {error_msg}")
    
    users = []
    if dataset_id:
        try:
            users = fetch_users_from_dataset(apify_client, dataset_id)
        except Exception:
            pass
    
    return build_result_dict(subreddit, "error", users, error=error_msg)


def fetch_partial_results_on_timeout(apify_client, run_id: str) -> List[Dict[str, Any]]:
    """Fetch partial results when run times out."""
    users = []
    if apify_client:
        try:
            run_status_response = apify_client.run(run_id).get()
            dataset_id = run_status_response.get("defaultDatasetId")
            if dataset_id:
                users = fetch_users_from_dataset(apify_client, dataset_id)
        except Exception:
            pass
    return users
