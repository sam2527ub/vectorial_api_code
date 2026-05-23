"""Utility for loading comments from S3."""
import asyncio
from typing import Dict, Any, List, Tuple
from app.config import logger
from app.utils.s3_utils import extract_s3_key_from_url


def is_valid_comment_for_tagging(comment: Dict[str, Any]) -> bool:
    """
    Check if comment has context_summary for dimension tagging.
    
    Valid ONLY if has context_summary (no fallback).
    """
    if not isinstance(comment, dict):
        return False
    
    # ONLY accept comments with context_summary
    context_summary = comment.get("context_summary")
    if context_summary and isinstance(context_summary, str) and context_summary.strip():
        return True
    
    return False


async def load_comments_from_profiles(
    storage_client, profiles: List[Any]
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """
    Load all comments from profiles and filter valid ones.
    
    Returns:
        Tuple of (all_valid_comments, profile_comments_map)
        - all_valid_comments: List of all valid comments across all profiles
        - profile_comments_map: Dict mapping profile_id -> {
            "profile": profile,
            "comments": [valid comments],
            "comments_key": s3_key,
            "all_comments_data": original comments data
        }
    """
    all_comments = []
    profile_comments_map = {}
    
    async def load_single_profile(profile: Any) -> None:
        profile_id = profile.id
        comments_s3_url = profile.commentsS3Url
        if not comments_s3_url:
            logger.debug(f"Profile {profile_id}: No commentsS3Url")
            return
        
        comments_key = extract_s3_key_from_url(comments_s3_url)
        if not comments_key:
            logger.debug(f"Profile {profile_id}: Could not extract S3 key from URL")
            return
        
        try:
            comments_data = await asyncio.to_thread(storage_client.read_json, comments_key)
            
            # Handle different data formats
            if isinstance(comments_data, list):
                comments = comments_data
            elif isinstance(comments_data, dict):
                comments = comments_data.get("comments", comments_data.get("data", []))
            else:
                logger.warning(f"Profile {profile_id}: Unexpected comments data format")
                return
            
            if not isinstance(comments, list):
                logger.warning(f"Profile {profile_id}: Comments is not a list")
                return
            
            # Filter valid comments (must have context_summary or context)
            valid_comments = [
                c for c in comments 
                if is_valid_comment_for_tagging(c)
            ]
            
            # Limit to comments_per_profile (if needed)
            # Note: We already limit in comment context summary service, but check anyway
            limited_comments = valid_comments[:40]  # Standard limit
            
            if limited_comments:
                all_comments.extend(limited_comments)
                profile_comments_map[profile_id] = {
                    "profile": profile,
                    "comments": limited_comments,
                    "comments_key": comments_key,
                    "all_comments_data": comments_data  # Preserve original structure for S3 update
                }
                logger.debug(
                    f"Profile {profile_id}: Loaded {len(limited_comments)}/{len(comments)} valid comments "
                    f"({len(comments) - len(valid_comments)} skipped - no context)"
                )
        except Exception as e:
            logger.error(f"Profile {profile_id}: Failed to load comments: {e}")
    
    await asyncio.gather(*[load_single_profile(p) for p in profiles])
    return all_comments, profile_comments_map