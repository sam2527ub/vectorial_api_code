"""Audience room endpoints."""
import copy
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, HTTPException, Path, Query
from app.models.schemas import CreateAudienceRoomRequest
from app.config import s3_client, s3_bucket, logger, dynamodb_resource
from app.utils.helpers import ensure_db_available
from app.utils.s3_utils import upload_json_to_s3, extract_s3_key_from_url, fetch_json_from_s3, get_s3_key_for_audience, ensure_enterprise_audience_folders_exist, get_source_audience_path, list_s3_objects_with_prefix, copy_s3_object, replace_enterprise_in_s3_url
from app import database
from app.database.enterprise_registry import get_all_enterprises
from app.services.audience_room_creation_service import request_handler as audience_room_creation_handler
from app.services.audience_group_summarization_service import request_handler as audience_group_summarization_handler

router = APIRouter()


@router.post("/api/v1/audience-rooms")
async def create_audience_room(payload: CreateAudienceRoomRequest):
    """
    Create an audience room, store its description and profile payloads in S3, and persist metadata in Postgres.
    - Stores audience description at: linkedin-audience/{enterpriseName}/{audience_room_id}/description.json
    - Stores each profile payload (with summary=null) at: linkedin-audience/{enterpriseName}/{audience_room_id}/profiles/{profile_id}/profile.json
    - If query and search_results provided: stores search results at: linkedin-audience/{enterpriseName}/{audience_room_id}/indexes.json
    
    Request Body:
    - enterpriseName (optional): Enterprise name to determine which database to use:
        - "gamma" -> uses GAMMA_DATABASE_URL
        - "app" -> uses APP_DATABASE_URL
        - "entelligence" -> uses ENTELLIGENCE_DATABASE_URL
        - "beta" -> uses BETA_DATABASE_URL
        - If not provided, uses AUDIENCE_DATABASE_URL
    """
    logger.info(f"=== CREATE AUDIENCE ROOM REQUEST START ===")
    logger.info(f"Request payload: enterpriseName={payload.enterpriseName}, room_name={payload.audience_room_name}, user_id={payload.userId}")
    logger.info(f"Request payload: profiles_count={len(payload.profiles)}, query={payload.query}, source={payload.source}")

    try:
        response = await audience_room_creation_handler.create_audience_room(payload)
        logger.info("=== CREATE AUDIENCE ROOM REQUEST SUCCESS ===")
        logger.info(f"Response: room_id={response['audience_room_id']}, profiles={response['profiles_created']}")
        return response
    except HTTPException:
        raise
    except Exception as e:
        logger.error("=== FATAL ERROR in create_audience_room ===")
        logger.error(f"Error creating audience room: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to create audience room: {str(e)}")


@router.post("/api/v1/audience-rooms/{audience_room_id}/generate-group-summary")
async def generate_group_summary(
    audience_room_id: str = Path(...),
    enterpriseName: Optional[str] = Query(None, description="Enterprise name (gamma, app, entelligence, beta). If not provided, uses default audience database.")
):
    """
    Generate a group summary and traits for an audience room based on all profile summaries.

    Flow:
    1. Fetch all profiles in the audience room
    2. For each profile, fetch description JSON from S3 and extract the summary
    3. Combine all profile summaries
    4. Generate a group summary using OpenAI based on the combined summaries
    5. Generate traits (5 traits with keywordTags and descriptions) based on profile summaries
    6. Update the audience room description JSON in S3 with both summary and traits fields

    Query Parameters:
    - enterpriseName (optional): Enterprise name to determine which database to use:
        - "gamma" -> uses GAMMA_DATABASE_URL
        - "app" -> uses APP_DATABASE_URL
        - "entelligence" -> uses ENTELLIGENCE_DATABASE_URL
        - "beta" -> uses BETA_DATABASE_URL
        - If not provided, uses AUDIENCE_DATABASE_URL
    """
    try:
        return await audience_group_summarization_handler.generate_group_summary(
            audience_room_id=audience_room_id,
            enterprise_name=enterpriseName,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating group summary: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to generate group summary: {str(e)}")


@router.post("/api/v1/audience-rooms/{audience_room_id}/remove-labels")
async def remove_labels_from_posts(
    audience_room_id: str = Path(...),
    enterpriseName: Optional[str] = Query(None, description="Enterprise name (gamma, app, entelligence, beta). If not provided, uses default audience database.")
):
    """
    Remove the 'labels' field from all posts JSON for all profiles in an audience room.
    
    Flow:
    1. Fetch all profiles in the audience room
    2. For each profile with postsS3Url:
       - Fetch posts JSON from S3
       - Remove 'labels' field from each post
       - Upload updated JSON back to S3
       - Update the profile record in the database
    
    Query Parameters:
    - enterpriseName (optional): Enterprise name to determine which database to use:
        - "gamma" -> uses GAMMA_DATABASE_URL
        - "app" -> uses APP_DATABASE_URL
        - "entelligence" -> uses ENTELLIGENCE_DATABASE_URL
        - "beta" -> uses BETA_DATABASE_URL
        - If not provided, uses AUDIENCE_DATABASE_URL
    """
    ensure_db_available("audience")
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")
    
    try:
        # Fetch audience room and profiles
        audience_room = database.find_audience_room_by_id(audience_room_id, include_profiles=True, enterprise_name=enterpriseName)
        if not audience_room:
            raise HTTPException(status_code=404, detail=f"Audience room {audience_room_id} not found")
        
        profiles = audience_room.profiles
        if not profiles:
            raise HTTPException(status_code=404, detail=f"No profiles found in audience room {audience_room_id}")
        
        logger.info(f"Removing labels from posts for {len(profiles)} profiles in audience room {audience_room_id}")
        
        processed_profiles = []
        total_posts_updated = 0
        profiles_skipped = 0
        profiles_with_errors = 0
        
        for profile in profiles:
            profile_id = profile.id
            profile_name = profile.profileName
            
            # Skip if no posts URL
            if not profile.postsS3Url:
                logger.warning(f"Profile {profile_id} ({profile_name}) has no posts URL, skipping")
                profiles_skipped += 1
                processed_profiles.append({
                    "profile_id": profile_id,
                    "profile_name": profile_name,
                    "status": "skipped",
                    "reason": "no_posts_url",
                    "posts_updated": 0
                })
                continue
            
            try:
                # Extract S3 key and fetch posts
                posts_key = extract_s3_key_from_url(profile.postsS3Url)
                if not posts_key:
                    logger.error(f"Invalid S3 URL format for profile {profile_id}: {profile.postsS3Url}")
                    profiles_with_errors += 1
                    processed_profiles.append({
                        "profile_id": profile_id,
                        "profile_name": profile_name,
                        "status": "error",
                        "reason": "invalid_s3_url",
                        "posts_updated": 0
                    })
                    continue
                
                # Fetch posts JSON from S3
                posts_data = fetch_json_from_s3(posts_key)
                
                # Extract posts array (could be in different formats)
                posts = []
                original_structure = None
                if isinstance(posts_data, dict):
                    posts = posts_data.get("posts", [])
                    if not posts and isinstance(posts_data.get("data"), list):
                        posts = posts_data["data"]
                    original_structure = posts_data
                elif isinstance(posts_data, list):
                    posts = posts_data
                    original_structure = posts
                
                if not posts:
                    logger.warning(f"No posts found for profile {profile_id}")
                    profiles_skipped += 1
                    processed_profiles.append({
                        "profile_id": profile_id,
                        "profile_name": profile_name,
                        "status": "skipped",
                        "reason": "no_posts",
                        "posts_updated": 0
                    })
                    continue
                
                # Remove 'labels' field from each post
                posts_updated_count = 0
                for post in posts:
                    if isinstance(post, dict) and "labels" in post:
                        del post["labels"]
                        posts_updated_count += 1
                
                if posts_updated_count == 0:
                    logger.info(f"No labels found in posts for profile {profile_id}")
                    processed_profiles.append({
                        "profile_id": profile_id,
                        "profile_name": profile_name,
                        "status": "skipped",
                        "reason": "no_labels_found",
                        "posts_updated": 0
                    })
                    continue
                
                # Update the posts data structure
                if isinstance(original_structure, dict):
                    original_structure["posts"] = posts
                    updated_posts_data = original_structure
                else:
                    updated_posts_data = posts
                
                # Upload updated posts back to S3
                updated_posts_url = upload_json_to_s3(posts_key, updated_posts_data)
                
                # Update profile record with new posts URL
                database.update_audience_profile(profile_id, {"postsS3Url": updated_posts_url}, enterprise_name=enterpriseName)
                
                total_posts_updated += posts_updated_count
                processed_profiles.append({
                    "profile_id": profile_id,
                    "profile_name": profile_name,
                    "status": "success",
                    "posts_updated": posts_updated_count,
                    "updated_posts_url": updated_posts_url
                })
                
                logger.info(f"Removed labels from {posts_updated_count} posts for profile {profile_id}")
                
            except Exception as e:
                logger.error(f"Error processing profile {profile_id}: {e}")
                profiles_with_errors += 1
                processed_profiles.append({
                    "profile_id": profile_id,
                    "profile_name": profile_name,
                    "status": "error",
                    "reason": str(e),
                    "posts_updated": 0
                })
        
        return {
            "audience_room_id": audience_room_id,
            "audience_room_name": audience_room.name,
            "total_profiles": len(profiles),
            "total_posts_updated": total_posts_updated,
            "profiles_processed": len([p for p in processed_profiles if p["status"] == "success"]),
            "profiles_skipped": profiles_skipped,
            "profiles_with_errors": profiles_with_errors,
            "profiles": processed_profiles
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error removing labels from posts: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to remove labels from posts: {str(e)}")