"""Apify dataset operations and utilities."""
from typing import Dict, Any, List
from app.config import logger


def get_item_count(apify_client, dataset_id: str, max_users_limit: int) -> int:
    """Get item count from dataset."""
    if not apify_client:
        return 0
    try:
        dataset_info = apify_client.dataset(dataset_id).get()
        if isinstance(dataset_info, dict):
            return dataset_info.get("itemCount") or dataset_info.get("data", {}).get("itemCount", 0)
    except Exception:
        try:
            dataset_client = apify_client.dataset(dataset_id)
            count = 0
            for _ in dataset_client.iterate_items():
                count += 1
                if count >= max_users_limit:
                    break
            return count
        except Exception:
            logger.debug(f"Could not get item count for dataset {dataset_id}")
            return 0
    return 0


def fetch_users_from_dataset(apify_client, dataset_id: str) -> List[Dict[str, Any]]:
    """Fetch all users from Apify dataset."""
    users = []
    if not apify_client:
        return users
    try:
        dataset_client = apify_client.dataset(dataset_id)
        for item in dataset_client.iterate_items():
            users.append(item)
    except Exception as e:
        logger.error(f"Error fetching users from dataset {dataset_id}: {e}")
    return users


def should_abort_for_max_users(item_count: int, max_users: int) -> bool:
    """Abort when dataset size reaches max users per subreddit."""
    return item_count >= max_users


def build_result_dict(
    subreddit: str,
    status: str,
    users: List[Dict[str, Any]],
    run_id: str = None,
    error: str = None,
    estimated_cost: float = None,
    aborted: bool = False,
    abort_reason: str = None
) -> Dict[str, Any]:
    """Build result dictionary."""
    result = {
        "subreddit": subreddit,
        "status": status,
        "users": users,
        "users_count": len(users)
    }
    if run_id:
        result["run_id"] = run_id
    if error:
        result["error"] = error
    if estimated_cost is not None:
        result["estimated_cost"] = round(estimated_cost, 3)
    if aborted:
        result["aborted"] = True
        result["abort_reason"] = abort_reason
    return result
