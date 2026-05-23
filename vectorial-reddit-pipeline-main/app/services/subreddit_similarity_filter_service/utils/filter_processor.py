"""Main filtering processor for subreddit similarity."""
from typing import Dict, List, Set, Any
from app.config import logger
from app.services.subreddit_similarity_filter_service.utils.data_extractors import (
    extract_username_from_user,
    extract_subreddit_from_activity,
    extract_username_from_activity,
    normalize_subreddit_name,
)
from app.services.subreddit_similarity_filter_service.config import SubredditFilterConfig


class FilterProcessor:
    """Processes user filtering by subreddit similarity."""
    
    def __init__(self, similarity_checker: "SimilarityChecker"):
        """Initialize filter processor."""
        self.similarity_checker = similarity_checker
        self.config = SubredditFilterConfig()
    
    def group_activities_by_username(
        self,
        activities: List[Dict[str, Any]]
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Group activities by username."""
        user_activities_map: Dict[str, List[Dict[str, Any]]] = {}
        for activity in activities:
            username = extract_username_from_activity(activity)
            if username:
                if username not in user_activities_map:
                    user_activities_map[username] = []
                user_activities_map[username].append(activity)
        
        logger.info(f"Grouped activities for {len(user_activities_map)} users")
        return user_activities_map
    
    def extract_user_subreddits(
        self,
        users: List[Dict[str, Any]],
        user_activities_map: Dict[str, List[Dict[str, Any]]]
    ) -> tuple:
        """Extract unique subreddits for each user."""
        all_user_subreddits: Set[str] = set()
        user_subreddit_map: Dict[str, Set[str]] = {}
        
        for user in users:
            username = extract_username_from_user(user)
            if not username:
                continue
            
            user_activities = user_activities_map.get(username, [])
            if not user_activities:
                continue
            
            # Extract unique subreddits for this user
            user_subreddits = set()
            for activity in user_activities:
                subreddit = extract_subreddit_from_activity(activity)
                if subreddit:
                    normalized = normalize_subreddit_name(subreddit)
                    user_subreddits.add(normalized)
                    all_user_subreddits.add(normalized)
            
            if user_subreddits:
                user_subreddit_map[username] = user_subreddits
        
        logger.info(
            f"Found {len(all_user_subreddits)} unique user subreddits across all users"
        )
        return all_user_subreddits, user_subreddit_map
    
    async def filter_users(
        self,
        users: List[Dict[str, Any]],
        user_activities_map: Dict[str, List[Dict[str, Any]]],
        matched_subreddits: List[str],
        similarity_map: Dict[str, bool],
        min_activities: int
    ) -> tuple:
        """Filter users based on subreddit similarity."""
        # Normalize matched subreddits
        normalized_matched = [
            normalize_subreddit_name(s) for s in matched_subreddits
        ]
        matched_set = set(normalized_matched)
        
        filtered_users = []
        filtered_activities = []
        stats = {
            "total_users": len(users),
            "users_checked": 0,
            "users_filtered_out": 0,
            "users_passed": 0,
            "total_activities_after": 0
        }
        
        # Filter users and activities
        for user in users:
            username = extract_username_from_user(user)
            if not username:
                continue
            
            user_activities = user_activities_map.get(username, [])
            if not user_activities:
                continue
            
            stats["users_checked"] += 1
            
            # Filter activities to only include those from matching/similar subreddits
            filtered_user_activities = []
            for activity in user_activities:
                subreddit = extract_subreddit_from_activity(activity)
                if not subreddit:
                    continue
                
                normalized_sub = normalize_subreddit_name(subreddit)
                
                # Check if subreddit is exact match or similar
                is_match = (
                    normalized_sub in matched_set or  # Exact match
                    similarity_map.get(normalized_sub, False)  # Similar (from AI provider)
                )
                
                if is_match:
                    filtered_user_activities.append(activity)
            
            # Keep user if they have >= min_activities activities after filtering
            if len(filtered_user_activities) >= min_activities:
                filtered_users.append(user)
                filtered_activities.extend(filtered_user_activities)
                stats["users_passed"] += 1
            else:
                stats["users_filtered_out"] += 1
        
        stats["total_activities_after"] = len(filtered_activities)
        
        logger.info(
            f"Step 4 filtering complete: {stats['users_passed']}/{stats['users_checked']} "
            f"users passed, {stats['total_activities_after']} activities remaining"
        )
        
        return filtered_users, filtered_activities, stats
