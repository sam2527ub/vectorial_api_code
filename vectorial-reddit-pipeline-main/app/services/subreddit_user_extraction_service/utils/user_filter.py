"""User filtering utilities."""
from typing import Dict, Any, List


class UserFilter:
    """Filters users based on contribution criteria."""
    
    @staticmethod
    def extract_contribution_count(user: Dict[str, Any]) -> int:
        """Extract total contribution (posts + comments) from user data."""
        if not isinstance(user, dict):
            return 0
        
        # Try various field name combinations
        posts = (
            user.get("posts") or 
            user.get("postCount") or 
            user.get("num_posts") or 
            user.get("post_count") or
            user.get("numPosts") or
            0
        )
        
        comments = (
            user.get("comments") or 
            user.get("commentCount") or 
            user.get("num_comments") or 
            user.get("comment_count") or
            user.get("numComments") or
            0
        )
        
        # Convert to int if they're strings or None
        try:
            posts = int(posts) if posts is not None else 0
        except (ValueError, TypeError):
            posts = 0
        
        try:
            comments = int(comments) if comments is not None else 0
        except (ValueError, TypeError):
            comments = 0
        
        return posts + comments
    
    @staticmethod
    def filter_by_contribution(users: List[Dict[str, Any]], min_contribution: int = 2) -> List[Dict[str, Any]]:
        """Filter users by minimum contribution threshold."""
        filtered = []
        for user in users:
            contribution = UserFilter.extract_contribution_count(user)
            if contribution >= min_contribution:
                filtered.append(user)
        return filtered
