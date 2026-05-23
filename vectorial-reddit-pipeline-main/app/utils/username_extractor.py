"""Shared username extraction utilities."""
from typing import Dict, Any, Optional


def extract_username_from_user(user: Dict[str, Any]) -> Optional[str]:
    """Extract username from user object."""
    if not isinstance(user, dict):
        return None
    
    username = (
        user.get("username") or 
        user.get("name") or 
        user.get("user") or 
        user.get("userName") or 
        user.get("user_name")
    )
    
    if username and isinstance(username, str):
        username = username.replace("/u/", "").replace("u/", "").strip()
        return username if username else None
    
    return None


def extract_username_from_activity(activity: Dict[str, Any]) -> Optional[str]:
    """Extract username from activity object."""
    if not isinstance(activity, dict):
        return None
    
    username = (
        activity.get("username") or
        activity.get("author") or
        activity.get("authorName") or
        activity.get("user") or
        activity.get("userName")
    )
    
    if username and isinstance(username, str):
        username = username.replace("/u/", "").replace("u/", "").strip()
        return username if username else None
    
    return None
