"""Utility for updating comments in S3."""
import asyncio
from typing import Dict, Any
from app.config import logger


async def update_comments_in_s3(
    storage_client, profile_comments_map: Dict[str, Dict[str, Any]]
) -> None:
    """Update comments in S3 per profile. Comments are updated in-place, so we just save the data."""
    async def update_single_profile(profile_id: str, profile_data: Dict[str, Any]) -> None:
        try:
            comments_key = profile_data["comments_key"]
            all_comments_data = profile_data["all_comments_data"]
            
            # Comments are updated in-place, so all_comments_data already has the dimensions
            # Just need to ensure structure is correct
            if isinstance(all_comments_data, dict):
                # If it's a dict, make sure comments list exists
                if "comments" not in all_comments_data:
                    all_comments_data["comments"] = all_comments_data.get("data", [])
            
            await asyncio.to_thread(storage_client.write_json, comments_key, all_comments_data)
            logger.debug(f"Profile {profile_id}: Updated comments in S3")
        except Exception as e:
            logger.error(f"Profile {profile_id}: Failed to update S3: {e}")
    
    await asyncio.gather(*[update_single_profile(pid, pdata) for pid, pdata in profile_comments_map.items()])
