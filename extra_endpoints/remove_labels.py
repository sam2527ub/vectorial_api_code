"""Extra endpoint: Remove labels from posts in an audience room."""
from typing import Optional
from fastapi import APIRouter, HTTPException, Path, Query

from app.config import s3_client, s3_bucket, logger
from app.utils.helpers import ensure_db_available
from app.utils.s3_utils import upload_json_to_s3, extract_s3_key_from_url, fetch_json_from_s3
from app import database

router = APIRouter()


@router.post("/api/v1/audience-rooms/{audience_room_id}/remove-labels")
async def remove_labels_from_posts(
    audience_room_id: str = Path(...),
    enterpriseName: Optional[str] = Query(None, description="Enterprise name (gamma, app, entelligence, beta). If not provided, uses default audience database."),
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
    """
    ensure_db_available("audience")
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")

    try:
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

                posts_data = fetch_json_from_s3(posts_key)
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

                if isinstance(original_structure, dict):
                    original_structure["posts"] = posts
                    updated_posts_data = original_structure
                else:
                    updated_posts_data = posts

                updated_posts_url = upload_json_to_s3(posts_key, updated_posts_data)
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
