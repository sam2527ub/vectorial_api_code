"""Utility for loading posts from S3."""
import asyncio
from typing import Dict, Any, List, Tuple
from app.config import logger
from app.utils.s3_utils import extract_s3_key_from_url

# Supported post types across platforms
# Reddit: "post"
# LinkedIn: "article", "text", "image", "linkedinVideo"
SUPPORTED_POST_TYPES = {"post", "article", "text", "image", "linkedinVideo"}


async def load_posts_from_profiles(
    storage_client, profiles: List[Any]
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """Load all posts from profiles.
    
    Supports both Reddit (type="post") and LinkedIn (type="article", type="text", 
    type="image", type="linkedinVideo") posts.
    """
    all_posts = []
    profile_posts_map = {}
    
    async def load_single_profile(profile: Any) -> None:
        profile_id = profile.id
        posts_s3_url = profile.postsS3Url
        if not posts_s3_url:
            return
        
        posts_key = extract_s3_key_from_url(posts_s3_url)
        if not posts_key:
            return
        
        try:
            posts_data = await asyncio.to_thread(storage_client.read_json, posts_key)
            posts = posts_data if isinstance(posts_data, list) else posts_data.get("posts", posts_data.get("data", []))
            if not isinstance(posts, list):
                return
            
            # Accept posts from both Reddit (type="post") and LinkedIn (type="article", type="text", 
            # type="image", type="linkedinVideo")
            posts_only = [p for p in posts if p.get("type") in SUPPORTED_POST_TYPES]
            if posts_only:
                all_posts.extend(posts_only)
                profile_posts_map[profile_id] = {
                    "profile": profile, "posts": posts_only,
                    "posts_key": posts_key, "all_posts_data": posts_data
                }
        except Exception as e:
            logger.error(f"Profile {profile_id}: Failed to load posts: {e}")
    
    await asyncio.gather(*[load_single_profile(p) for p in profiles])
    return all_posts, profile_posts_map
