"""Result processing utilities for user activity scraping."""
from typing import Dict, Any, List
from datetime import datetime
from app.services.user_observation_fetch_service.utils.username_utils import UsernameProcessor


class ResultProcessor:
    """Processes and transforms scraping results."""
    
    def __init__(self):
        """Initialize result processor."""
        self.username_processor = UsernameProcessor()
    
    def transform_new_scraper_to_old_format(
        self,
        flat_activities: List[Dict[str, Any]], 
        requested_usernames: List[str]
    ) -> Dict[str, Any]:
        """
        Transform flat activities from epctex/reddit-scraper to the old grouped format.
        
        NEW format (input - flat list):
        [
            {"type": "comment", "author": "User1", "subreddit": "...", "body": "...", "createdAt": 123, ...},
            {"type": "post", "author": "User1", "title": "...", "text": "...", "createdAt": 456, ...},
            ...
        ]
        
        OLD format (output - grouped by user):
        {
            "users": [{"username": "User1", "activities_count": 5, "status": "success"}, ...],
            "activities": [{"type": "comment", "username": "User1", "subreddit": "...", "created_utc": 123, ...}, ...]
        }
        
        This transformation ensures downstream steps (4, 5, 6) work without modification.
        """
        # Initialize map with requested usernames (tracks users with 0 activities)
        user_activities_map: Dict[str, List[Dict[str, Any]]] = {}
        for username in requested_usernames:
            clean_name = self.username_processor.normalize_username(username)
            if clean_name:
                user_activities_map[clean_name] = []
        
        # Transform and group activities
        transformed_activities = []
        
        for activity in flat_activities:
            if not isinstance(activity, dict):
                continue
            
            author = activity.get("author")
            if not author:
                continue
            
            # Build transformed activity matching old format
            transformed_activity = {
                "type": activity.get("type"),
                "subreddit": activity.get("subreddit"),
                "url": activity.get("url"),
                "created_utc": activity.get("createdAt"),  # Map createdAt -> created_utc
                "username": author,  # Add username field for compatibility
                "author": author,  # Keep author for new code compatibility
            }
            
            if activity.get("type") == "post":
                transformed_activity.update({
                    "title": activity.get("title"),
                    "body": activity.get("text"),  # Map text -> body
                    "text": activity.get("text"),  # Keep text too
                    "score": activity.get("score"),
                    "upvoteRatio": activity.get("upvoteRatio"),
                    "commentCount": activity.get("commentCount"),
                    "id": activity.get("id"),
                    "flair": activity.get("flair"),
                    "isOver18": activity.get("isOver18"),
                    "post_details": {
                        "title": activity.get("title"),
                        "body": activity.get("text"),
                        "comments": []  # New scraper doesn't return nested comments
                    }
                })
            elif activity.get("type") == "comment":
                transformed_activity.update({
                    "body": activity.get("body"),
                    "score": activity.get("score"),
                    "id": activity.get("id"),
                    "postUrl": activity.get("postUrl"),
                    "postTitle": activity.get("postTitle"),
                    "postId": activity.get("postId"),
                    "parentId": activity.get("parentId"),
                    "isSubmitter": activity.get("isSubmitter"),
                })
            
            transformed_activities.append(transformed_activity)
            
            # Group by author
            if author not in user_activities_map:
                user_activities_map[author] = []
            user_activities_map[author].append(transformed_activity)
        
        # Build user results in old format
        user_results = []
        successful_users = 0
        failed_users = 0
        
        for username, activities in user_activities_map.items():
            if activities:
                successful_users += 1
                user_results.append({
                    "username": username,
                    "activities_count": len(activities),
                    "status": "success"
                })
            else:
                failed_users += 1
                user_results.append({
                    "username": username,
                    "activities_count": 0,
                    "status": "error",
                    "error": "No activities found for this user"
                })
        
        return {
            "total_users": len(user_activities_map),
            "successful_users": successful_users,
            "failed_users": failed_users,
            "total_activities": len(transformed_activities),
            "users": user_results,
            "activities": transformed_activities,
            "completed_at": datetime.now().isoformat()
        }
