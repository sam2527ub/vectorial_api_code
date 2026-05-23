"""Utility for updating posts in S3."""
import asyncio
from typing import Dict, Any
from app.config import logger


async def update_posts_in_s3(
    storage_client, profile_posts_map: Dict[str, Dict[str, Any]]
) -> None:
    """Update posts in S3 per profile."""
    async def update_single_profile(profile_id: str, profile_data: Dict[str, Any]) -> None:
        try:
            posts_key = profile_data["posts_key"]
            all_posts_data = profile_data["all_posts_data"]
            if isinstance(all_posts_data, dict):
                all_posts_data["posts"] = all_posts_data.get("posts", all_posts_data.get("data", []))
            await asyncio.to_thread(storage_client.write_json, posts_key, all_posts_data)
        except Exception as e:
            logger.error(f"Profile {profile_id}: Failed to update S3: {e}")
    
    await asyncio.gather(*[update_single_profile(pid, pdata) for pid, pdata in profile_posts_map.items()])
