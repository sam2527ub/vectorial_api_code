"""S3 data fetching utilities for profile data."""
from typing import Dict, Any, List, Any as AnyType
from app.config import logger
from app.utils.s3_utils import extract_s3_key_from_url, fetch_json_from_s3
from app.services.user_profile_summarization_service.clients.storage.factory import get_storage_client
from app.services.user_profile_summarization_service.utils.activity_utils import sort_and_limit_comments


class S3DataFetcher:
    """Handles fetching profile data from S3."""
    
    def __init__(self):
        """Initialize S3 data fetcher."""
        self.storage_client = get_storage_client()
    
    def fetch_profile_description(
        self,
        profile: AnyType,
        profile_id: str
    ) -> Dict[str, Any]:
        """Fetch profile description from S3."""
        if not profile.profileDescriptionS3Url:
            return None
        
        profile_key = extract_s3_key_from_url(profile.profileDescriptionS3Url)
        if not profile_key:
            return None
        
        try:
            return fetch_json_from_s3(
                profile_key,
                s3_client=self.storage_client.get_raw_client(),
                s3_bucket=self.storage_client.get_bucket_name()
            )
        except Exception as e:
            logger.warning(f"Profile {profile_id}: Failed to fetch description: {e}")
            return None
    
    def fetch_posts(self, profile: AnyType, profile_id: str) -> List[Dict[str, Any]]:
        """Fetch posts from S3."""
        if not profile.postsS3Url:
            return []
        
        posts_key = extract_s3_key_from_url(profile.postsS3Url)
        if not posts_key:
            return []
        
        try:
            posts_data = fetch_json_from_s3(
                posts_key,
                s3_client=self.storage_client.get_raw_client(),
                s3_bucket=self.storage_client.get_bucket_name()
            )
            return self._extract_items_from_data(posts_data, "posts")
        except Exception as e:
            logger.warning(f"Profile {profile_id}: Failed to fetch posts: {e}")
            return []
    
    def fetch_comments(self, profile: AnyType, profile_id: str) -> List[Dict[str, Any]]:
        """Fetch comments from S3."""
        if not profile.commentsS3Url:
            return []
        
        comments_key = extract_s3_key_from_url(profile.commentsS3Url)
        if not comments_key:
            return []
        
        try:
            comments_data = fetch_json_from_s3(
                comments_key,
                s3_client=self.storage_client.get_raw_client(),
                s3_bucket=self.storage_client.get_bucket_name()
            )
            comments = self._extract_items_from_data(comments_data, "comments")
            
            # Sort and limit comments
            if comments:
                comments = sort_and_limit_comments(comments, limit=40, profile_id=profile_id)
            
            return comments
        except Exception as e:
            logger.warning(f"Profile {profile_id}: Failed to fetch comments: {e}")
            return []
    
    def fetch_activities(
        self,
        profile: AnyType,
        profile_id: str
    ) -> List[Dict[str, Any]]:
        """Fetch and combine posts and comments from S3."""
        posts = self.fetch_posts(profile, profile_id)
        comments = self.fetch_comments(profile, profile_id)
        return posts + comments
    
    def _extract_items_from_data(self, data: Any, key: str) -> List[Dict[str, Any]]:
        """Extract items array from S3 data (handles different formats)."""
        if isinstance(data, dict):
            items = data.get(key, [])
            if not items and isinstance(data.get("data"), list):
                items = data["data"]
            return items if isinstance(items, list) else []
        elif isinstance(data, list):
            return data
        return []
