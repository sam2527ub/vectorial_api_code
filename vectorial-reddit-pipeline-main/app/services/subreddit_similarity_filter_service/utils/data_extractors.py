"""Data extraction utilities for subreddits."""
import re
from typing import Dict, Any, Optional
from app.utils.username_extractor import (
    extract_username_from_user,
    extract_username_from_activity
)


def extract_subreddit_from_activity(activity: Dict[str, Any]) -> Optional[str]:
    """Extract subreddit name from activity object."""
    if not isinstance(activity, dict):
        return None
    
    subreddit = (
        activity.get("subreddit") or
        activity.get("subredditName") or
        activity.get("subreddit_name") or
        activity.get("community") or
        activity.get("communityName")
    )
    
    if subreddit and isinstance(subreddit, str):
        return subreddit.strip()
    
    # Try to extract from URL fields
    url = activity.get("url") or activity.get("link") or activity.get("permalink")
    if url and isinstance(url, str):
        match = re.search(r'/r/([^/?]+)', url)
        if match:
            return match.group(1)
    
    return None




def normalize_subreddit_name(subreddit: str) -> str:
    """Normalize subreddit name by removing r/ prefix and converting to lowercase."""
    subreddit = subreddit.strip()
    # Remove r/ prefix if present
    if subreddit.startswith('r/') or subreddit.startswith('/r/'):
        subreddit = subreddit.lstrip('/r/')
    # Extract from URL if present
    if '/r/' in subreddit:
        match = re.search(r'/r/([^/?]+)', subreddit)
        if match:
            subreddit = match.group(1)
    return subreddit.lower()
