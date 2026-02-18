"""Preview endpoints for audience room previews.

These endpoints manage the preview cache table for fast UI rendering.
Preview data includes room info, description summary, traits, and first 5 profiles.
"""
import logging
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, HTTPException, Query, Path
from app.config import logger, s3_client, s3_bucket
from app.utils.helpers import ensure_db_available
from app.utils.s3_utils import extract_s3_key_from_url, fetch_json_from_s3
from app import database

router = APIRouter()

# Number of profiles to include in preview
PREVIEW_PROFILE_LIMIT = 5


def _fetch_s3_json_safe(s3_url: Optional[str]) -> Dict[str, Any]:
    """Safely fetch JSON from S3, returning empty dict on failure."""
    if not s3_url:
        return {}
    try:
        key = extract_s3_key_from_url(s3_url)
        if not key:
            return {}
        return fetch_json_from_s3(key)
    except Exception as e:
        logger.warning(f"Failed to fetch S3 data from {s3_url}: {e}")
        return {}


def _build_profile_preview(profile: Dict[str, Any], source: str) -> Dict[str, Any]:
    """
    Build a profile preview object by fetching data from S3.
    Handles both LinkedIn and Reddit profile schemas.
    """
    profile_data = _fetch_s3_json_safe(profile.get('profileDescriptionS3Url'))
    
    # Determine source from profile or room
    profile_source = profile.get('source') or source or ''
    is_linkedin = profile_source.lower() == 'linkedin'
    
    if is_linkedin:
        # LinkedIn profile schema
        return {
            'id': profile.get('id'),
            'name': profile_data.get('name') or profile.get('profileName'),
            'linkedin_profile_url': profile_data.get('linkedin_profile_url') or profile.get('profileUrl'),
            'current_location': profile_data.get('current_location'),
            'current_company': profile_data.get('current_company'),
            'industry': profile_data.get('industry'),
            'summary': profile_data.get('summary'),
            'source': 'linkedin'
        }
    else:
        # Reddit profile schema
        return {
            'id': profile.get('id'),
            'name': profile_data.get('username') or profile.get('profileName'),
            'reddit_profile_url': profile_data.get('userUrl') or profile.get('profileUrl'),
            'post_count': profile_data.get('postCount'),
            'comment_count': profile_data.get('commentCount'),
            'summary': profile_data.get('summary'),
            'source': 'reddit'
        }


def _generate_preview_for_room(room: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generate preview data for a single room by fetching from S3.
    Returns the preview data ready to be stored in the database.
    """
    room_id = room.get('id')
    room_name = room.get('name')
    source = room.get('source') or ''
    user_id = room.get('userId') or 'default'
    profiles = room.get('profiles', [])
    total_profile_count = room.get('total_profile_count', len(profiles))
    
    # Fetch room description from S3
    room_description_data = _fetch_s3_json_safe(room.get('descriptionS3Url'))
    description_summary = room_description_data.get('summary')
    
    # Build profile previews
    profile_previews = []
    for profile in profiles[:PREVIEW_PROFILE_LIMIT]:
        try:
            preview = _build_profile_preview(profile, source)
            profile_previews.append(preview)
        except Exception as e:
            logger.warning(f"Failed to build preview for profile {profile.get('id')}: {e}")
            # Include basic info even if S3 fetch fails
            profile_previews.append({
                'id': profile.get('id'),
                'name': profile.get('profileName'),
                'summary': None,
                'source': source.lower() if source else 'unknown'
            })
    
    return {
        'room_id': room_id,
        'name': room_name,
        'user_id': user_id,
        'description_summary': description_summary,
        'source': source.lower() if source else None,
        'total_profile_count': total_profile_count,
        'profiles': profile_previews
    }


@router.post("/api/v1/previews/update-room/{room_id}")
async def update_preview_for_room(
    room_id: str = Path(..., description="The audience room ID to update preview for"),
    enterpriseName: Optional[str] = Query(None, description="Enterprise name (gamma, app, entelligence, beta). If not provided, uses default audience database.")
):
    """
    Update or create preview for a specific audience room.
    
    This endpoint fetches the room data from the database and S3,
    then creates/updates the preview cache for fast UI rendering.
    
    Handles both LinkedIn and Reddit audience rooms automatically
    based on the room's source field.
    
    Use this after:
    - Creating a new audience room
    - Generating group summary
    - Any update to room or profile data
    
    Query Parameters:
    - enterpriseName (optional): Enterprise name to determine which database to use:
        - "gamma" -> uses GAMMA_DATABASE_URL
        - "app" -> uses APP_DATABASE_URL
        - "entelligence" -> uses ENTELLIGENCE_DATABASE_URL
        - "beta" -> uses BETA_DATABASE_URL
        - If not provided, uses AUDIENCE_DATABASE_URL
    
    WARNING: This only modifies the previews table, no other tables are touched.
    """
    logger.info(f"=== UPDATE PREVIEW FOR ROOM REQUEST START ===")
    logger.info(f"Request: room_id={room_id}, enterprise={enterpriseName}")
    
    ensure_db_available("audience")
    logger.info("Database availability check passed")
    
    if not s3_client or not s3_bucket:
        logger.error("S3 not configured")
        raise HTTPException(status_code=503, detail="S3 is not configured")
    
    logger.info("S3 check passed")
    
    try:
        # Ensure preview table exists with proper schema
        logger.info(f"Ensuring preview table exists (enterprise={enterpriseName})")
        database.ensure_preview_table_exists(enterprise_name=enterpriseName)
        
        # Fetch room with profiles from database (READ-ONLY)
        # Use find_audience_room_by_id instead of find_audience_room_with_profiles_for_preview
        # since it supports enterprise_name
        logger.info(f"Fetching audience room {room_id} with profiles (enterprise={enterpriseName})")
        room_obj = database.find_audience_room_by_id(room_id, include_profiles=True, enterprise_name=enterpriseName)
        
        if not room_obj:
            logger.warning(f"Audience room {room_id} not found (enterprise={enterpriseName})")
            raise HTTPException(status_code=404, detail=f"Audience room {room_id} not found")
        
        logger.info(f"Found audience room: id={room_obj.id}, name={room_obj.name}, profiles_count={len(room_obj.profiles)}")
        
        # Convert room object to dict format for _generate_preview_for_room
        room = {
            'id': room_obj.id,
            'name': room_obj.name,
            'descriptionS3Url': room_obj.descriptionS3Url,
            'source': room_obj.source,
            'userId': room_obj.userId,
            'profiles': [
                {
                    'id': p.id,
                    'profileName': p.profileName,
                    'profileUrl': p.profileUrl,
                    'profileDescriptionS3Url': p.profileDescriptionS3Url,
                    'source': p.source
                }
                for p in room_obj.profiles[:PREVIEW_PROFILE_LIMIT]
            ],
            'total_profile_count': len(room_obj.profiles)
        }
        
        # Generate preview data
        logger.info(f"Generating preview data for room {room_id}")
        preview_data = _generate_preview_for_room(room)
        logger.info(f"Preview data generated: profiles_count={len(preview_data.get('profiles', []))}, total_profiles={preview_data.get('total_profile_count', 0)}")
        
        # Upsert preview (only touches previews table)
        logger.info(f"Upserting preview to database (enterprise={enterpriseName})")
        result = database.upsert_preview(
            room_id=preview_data['room_id'],
            name=preview_data['name'],
            user_id=preview_data['user_id'],
            description_summary=preview_data['description_summary'],
            source=preview_data['source'],
            total_profile_count=preview_data['total_profile_count'],
            profiles=preview_data['profiles'],
            enterprise_name=enterpriseName
        )
        
        logger.info(f"Successfully updated preview for room {room_id}")
        logger.info(f"=== UPDATE PREVIEW FOR ROOM REQUEST SUCCESS ===")
        
        return {
            "status": "success",
            "room_id": room_id,
            "room_name": preview_data['name'],
            "source": preview_data['source'],
            "total_profile_count": preview_data['total_profile_count'],
            "preview_profiles_count": len(preview_data['profiles']),
            "has_summary": bool(preview_data['description_summary'])
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating preview for room {room_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update preview: {str(e)}")

