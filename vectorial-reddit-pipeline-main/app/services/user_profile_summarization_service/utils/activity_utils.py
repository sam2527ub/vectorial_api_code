"""Activity processing utilities."""
from datetime import datetime
from typing import List, Dict, Any, Optional
from app.config import logger


def extract_activity_texts(activities: List[Dict[str, Any]]) -> List[str]:
    """Extract text content from activities list."""
    activity_texts = []
    for activity in activities:
        content = (
            activity.get("content") or 
            activity.get("text") or 
            activity.get("body") or 
            ""
        )
        if content and content.strip():
            activity_texts.append(content.strip())
    
    return activity_texts


def get_timestamp(comment: Dict[str, Any]) -> float:
    """Extract timestamp from comment for sorting."""
    created_at = comment.get("createdAt") or comment.get("created_at") or 0
    
    if isinstance(created_at, (int, float)):
        return float(created_at)
    
    if isinstance(created_at, str):
        try:
            return datetime.fromisoformat(
                created_at.replace('Z', '+00:00')
            ).timestamp()
        except (ValueError, AttributeError):
            return 0.0
    
    return 0.0


def sort_and_limit_comments(
    comments: List[Dict[str, Any]], 
    limit: int = 40,
    profile_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Sort comments by creation date (latest first) and limit count."""
    if not comments:
        return []
    
    original_count = len(comments)
    
    # Sort by createdAt descending (latest first)
    sorted_comments = sorted(comments, key=get_timestamp, reverse=True)
    
    # Limit to latest N comments
    limited_comments = sorted_comments[:limit]
    
    if original_count > limit and profile_id:
        logger.info(
            f"Profile {profile_id}: Limited to latest {len(limited_comments)} "
            f"comments (from {original_count} total)"
        )
    
    return limited_comments
