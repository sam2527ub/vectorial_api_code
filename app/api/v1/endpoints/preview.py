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
    traits = room_description_data.get('traits')
    
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
        'traits': traits,
        'profiles': profile_previews
    }


@router.get("/api/v1/previews")
async def get_all_previews(user_id: Optional[str] = Query(None, description="Filter previews by user ID")):
    """
    Get all audience room previews from the Preview table.
    
    Returns cached preview data with room info and first 5 profiles for each room.
    This is much faster than fetching from S3 as it reads directly from the database.
    
    Query Parameters:
    - user_id (optional): Filter previews by user ID
    
    Response includes:
    - room_id: UUID of the audience room
    - user_id: User ID who owns this preview
    - name: Room name
    - source: Source type (linkedin/reddit)
    - description_summary: Summary from room description (if available)
    - total_profile_count: Total number of profiles in the room
    - traits: Array of trait objects
    - profiles: Array of first 5 profiles with their details
    - created_at: Timestamp when preview was created
    - updated_at: Timestamp when preview was last updated
    """
    ensure_db_available("audience")
    
    try:
        previews = database.find_all_previews(user_id=user_id)
        
        return {
            "count": len(previews),
            "user_id": user_id,
            "previews": previews
        }
    except Exception as e:
        logger.error(f"Error fetching previews: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch previews: {str(e)}")


@router.get("/api/v1/previews/{room_id}")
async def get_preview_by_room_id(
    room_id: str,
    user_id: Optional[str] = Query(None, description="Filter by user ID")
):
    """
    Get a single audience room preview by room ID.
    
    Returns cached preview data for a specific room with first 5 profiles.
    
    Query Parameters:
    - user_id (optional): Filter by user ID
    
    Response includes:
    - room_id: UUID of the audience room
    - user_id: User ID who owns this preview
    - name: Room name
    - source: Source type (linkedin/reddit)
    - description_summary: Summary from room description (if available)
    - total_profile_count: Total number of profiles in the room
    - traits: Array of trait objects
    - profiles: Array of first 5 profiles with their details
    - created_at: Timestamp when preview was created
    - updated_at: Timestamp when preview was last updated
    """
    ensure_db_available("audience")
    
    try:
        preview = database.find_preview_by_room_id(room_id, user_id=user_id)
        
        if not preview:
            if user_id:
                raise HTTPException(status_code=404, detail=f"Preview not found for room {room_id} and user {user_id}")
            else:
                raise HTTPException(status_code=404, detail=f"Preview not found for room {room_id}")
        
        return preview
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching preview for room {room_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch preview: {str(e)}")


@router.post("/api/v1/previews/update-room/{room_id}")
async def update_preview_for_room(
    room_id: str = Path(..., description="The audience room ID to update preview for")
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
    
    WARNING: This only modifies the previews table, no other tables are touched.
    """
    ensure_db_available("audience")
    
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured")
    
    try:
        # Ensure preview table exists with proper schema
        database.ensure_preview_table_exists()
        
        # Fetch room with profiles from database (READ-ONLY)
        room = database.find_audience_room_with_profiles_for_preview(room_id, PREVIEW_PROFILE_LIMIT)
        
        if not room:
            raise HTTPException(status_code=404, detail=f"Audience room {room_id} not found")
        
        # Generate preview data
        preview_data = _generate_preview_for_room(room)
        
        # Upsert preview (only touches previews table)
        result = database.upsert_preview(
            room_id=preview_data['room_id'],
            name=preview_data['name'],
            user_id=preview_data['user_id'],
            description_summary=preview_data['description_summary'],
            source=preview_data['source'],
            total_profile_count=preview_data['total_profile_count'],
            traits=preview_data['traits'],
            profiles=preview_data['profiles']
        )
        
        logger.info(f"Successfully updated preview for room {room_id}")
        
        return {
            "status": "success",
            "room_id": room_id,
            "room_name": preview_data['name'],
            "source": preview_data['source'],
            "total_profile_count": preview_data['total_profile_count'],
            "preview_profiles_count": len(preview_data['profiles']),
            "has_summary": bool(preview_data['description_summary']),
            "has_traits": bool(preview_data['traits'])
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating preview for room {room_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update preview: {str(e)}")


@router.post("/api/v1/previews/populate-all")
async def populate_all_previews():
    """
    Populate previews for ALL audience rooms in the database.
    
    This endpoint:
    1. Ensures the preview table exists with the proper schema
    2. Fetches all audience rooms from the database
    3. For each room, fetches data from S3 and creates/updates preview
    
    Use this for:
    - Initial population of the preview table
    - Refreshing all preview data after schema changes
    
    WARNING: This only modifies the previews table, no other tables are touched.
    All reads from AudienceRoom and AudienceProfile tables are READ-ONLY.
    """
    ensure_db_available("audience")
    
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured")
    
    try:
        # Ensure preview table exists with proper schema
        database.ensure_preview_table_exists()
        
        # Fetch all rooms with profiles (READ-ONLY)
        rooms = database.find_all_audience_rooms_with_profiles(limit=PREVIEW_PROFILE_LIMIT)
        
        if not rooms:
            return {
                "status": "success",
                "message": "No audience rooms found",
                "total_rooms": 0,
                "successful": 0,
                "failed": 0,
                "rooms": []
            }
        
        logger.info(f"Populating previews for {len(rooms)} rooms...")
        
        results = []
        successful = 0
        failed = 0
        
        for room in rooms:
            room_id = room.get('id')
            try:
                # Generate preview data
                preview_data = _generate_preview_for_room(room)
                
                # Upsert preview (only touches previews table)
                database.upsert_preview(
                    room_id=preview_data['room_id'],
                    name=preview_data['name'],
                    user_id=preview_data['user_id'],
                    description_summary=preview_data['description_summary'],
                    source=preview_data['source'],
                    total_profile_count=preview_data['total_profile_count'],
                    traits=preview_data['traits'],
                    profiles=preview_data['profiles']
                )
                
                results.append({
                    "room_id": room_id,
                    "room_name": preview_data['name'],
                    "source": preview_data['source'],
                    "status": "success",
                    "profile_count": preview_data['total_profile_count']
                })
                successful += 1
                
            except Exception as e:
                logger.error(f"Failed to populate preview for room {room_id}: {e}")
                results.append({
                    "room_id": room_id,
                    "room_name": room.get('name'),
                    "status": "failed",
                    "error": str(e)
                })
                failed += 1
        
        logger.info(f"Preview population complete: {successful} successful, {failed} failed")
        
        return {
            "status": "success",
            "message": f"Populated previews for {successful} rooms",
            "total_rooms": len(rooms),
            "successful": successful,
            "failed": failed,
            "rooms": results
        }
        
    except Exception as e:
        logger.error(f"Error populating all previews: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to populate previews: {str(e)}")


@router.delete("/api/v1/previews/{room_id}")
async def delete_preview(
    room_id: str = Path(..., description="The room ID to delete preview for"),
    user_id: Optional[str] = Query(None, description="Optional user ID to scope deletion")
):
    """
    Delete a preview record.
    
    WARNING: This only deletes from the previews table, no other tables are touched.
    """
    ensure_db_available("audience")
    
    try:
        deleted = database.delete_preview(room_id, user_id)
        
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Preview not found for room {room_id}")
        
        return {
            "status": "success",
            "message": f"Preview deleted for room {room_id}",
            "room_id": room_id
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting preview for room {room_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete preview: {str(e)}")

