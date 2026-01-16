"""Summary service for profile and group summaries."""
import logging
from typing import Dict, Any, Optional, Any as AnyType
from fastapi import HTTPException
from app.config import openai_client, logger, s3_client, s3_bucket
from app.services.openai_service import generate_profile_summary_from_posts
from app.utils.s3_utils import upload_json_to_s3, extract_s3_key_from_url, fetch_json_from_s3
from app import database


async def process_profile_summary(
    profile: AnyType,
    audience_room_id: str,
    enterprise_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Process a single profile: fetch posts, generate summary, and update description JSON.
    
    Returns:
        Dictionary with processing results
    """
    profile_id = profile.id
    profile_name = profile.profileName
    
    try:
        # Fetch profile description JSON from S3
        if not profile.profileDescriptionS3Url:
            return {
                "profile_id": profile_id,
                "profile_name": profile_name,
                "status": "skipped",
                "reason": "no_description_url",
                "error": None
            }
        
        profile_key = extract_s3_key_from_url(profile.profileDescriptionS3Url)
        if not profile_key:
            return {
                "profile_id": profile_id,
                "profile_name": profile_name,
                "status": "error",
                "reason": "invalid_description_url",
                "error": "Invalid S3 URL format"
            }
        
        profile_data = fetch_json_from_s3(profile_key)
        
        # Extract profile info for the prompt
        profile_title = None
        profile_company = profile_data.get("current_company")
        
        # Fetch posts from S3
        if not profile.postsS3Url:
            return {
                "profile_id": profile_id,
                "profile_name": profile_name,
                "status": "skipped",
                "reason": "no_posts_url",
                "error": None
            }
        
        posts_key = extract_s3_key_from_url(profile.postsS3Url)
        if not posts_key:
            return {
                "profile_id": profile_id,
                "profile_name": profile_name,
                "status": "error",
                "reason": "invalid_posts_url",
                "error": "Invalid S3 URL format for posts"
            }
        
        posts_data = fetch_json_from_s3(posts_key)
        
        # Extract posts array
        posts = []
        if isinstance(posts_data, dict):
            posts = posts_data.get("posts", [])
            if not posts and isinstance(posts_data.get("data"), list):
                posts = posts_data["data"]
        elif isinstance(posts_data, list):
            posts = posts_data
        
        if not posts:
            return {
                "profile_id": profile_id,
                "profile_name": profile_name,
                "status": "skipped",
                "reason": "no_posts",
                "error": None
            }
        
        # Generate summary, keywords, and highlights
        summary_result = await generate_profile_summary_from_posts(
            profile_id=profile_id,
            profile_name=profile_name,
            profile_title=profile_title,
            profile_company=profile_company,
            posts=posts,
        )
        
        # Validate that summary generation actually succeeded
        summary_text = summary_result.get("summary")
        if not summary_text or not summary_text.strip():
            logger.error(f"Profile {profile_id} ({profile_name}): Summary generation returned empty result")
            return {
                "profile_id": profile_id,
                "profile_name": profile_name,
                "status": "error",
                "reason": "empty_summary",
                "error": "OpenAI API call failed or returned empty summary. Check logs for details."
            }
        
        # Update profile description JSON
        profile_data["summary"] = summary_result["summary"]
        profile_data["highlights"] = summary_result["highlights"]
        profile_data["keywords"] = summary_result["keywords"]
        
        # Upload updated profile description back to S3
        updated_profile_url = upload_json_to_s3(profile_key, profile_data)
        
        # Update the profile record with the new URL
        database.update_audience_profile(profile_id, {"profileDescriptionS3Url": updated_profile_url}, enterprise_name=enterprise_name)
        
        return {
            "profile_id": profile_id,
            "profile_name": profile_name,
            "status": "success",
            "summary": summary_result["summary"][:100] + "..." if summary_result["summary"] and len(summary_result["summary"]) > 100 else summary_result["summary"],
            "highlights_count": len(summary_result["highlights"]),
            "keywords_count": len(summary_result["keywords"]),
            "error": None
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing profile {profile_id}: {e}")
        return {
            "profile_id": profile_id,
            "profile_name": profile_name,
            "status": "error",
            "reason": "processing_failed",
            "error": str(e)
        }

