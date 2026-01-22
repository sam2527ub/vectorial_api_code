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
from app.models.schemas import UpdatePreviewNameRequest

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


@router.get("/api/v1/previews")
async def get_previews(
    userId: Optional[str] = Query(None, description="User ID to filter previews by"),
    enterpriseName: Optional[str] = Query(None, description="Enterprise name (gamma, app, entelligence, beta). If not provided, uses default audience database.")
):
    """
    Get all audience room previews from the Preview table.
    
    This endpoint fetches preview data from the database based on the enterprise name.
    This is a READ-ONLY operation - no tables are modified.
    
    Query Parameters:
    - userId (optional): User ID to filter previews by. If provided, only returns previews for that user.
    - enterpriseName (optional): Enterprise name to determine which database to query:
        - "gamma" -> uses GAMMA_DATABASE_URL
        - "app" -> uses APP_DATABASE_URL
        - "entelligence" -> uses ENTELLIGENCE_DATABASE_URL
        - "beta" -> uses BETA_DATABASE_URL
        - If not provided, uses AUDIENCE_DATABASE_URL
    
    Response includes:
    - count: Number of previews returned
    - enterpriseName: Enterprise name used (if any)
    - userId: User ID filter used (if any)
    - previews: Array of preview objects with:
        - room_id: UUID of the audience room
        - user_id: User ID who owns this preview
        - name: Room name
        - source: Source type (linkedin/reddit)
        - description_summary: Summary from room description (if available)
        - total_profile_count: Total number of profiles in the room
        - profiles: Array of first 5 profiles with their details
        - created_at: Timestamp when preview was created
        - updated_at: Timestamp when preview was last updated
    """
    logger.info(f"=== GET PREVIEWS REQUEST START ===")
    logger.info(f"Request: user_id={userId}, enterprise={enterpriseName}")
    
    ensure_db_available("audience")
    logger.info("Database availability check passed")
    
    try:
        logger.info(f"Fetching previews from database (user_id={userId}, enterprise={enterpriseName})")
        previews = database.find_all_previews(user_id=userId, enterprise_name=enterpriseName)
        
        logger.info(f"Found {len(previews)} previews")
        logger.info(f"=== GET PREVIEWS REQUEST SUCCESS ===")
        
        return {
            "count": len(previews),
            "enterpriseName": enterpriseName,
            "userId": userId,
            "previews": previews
        }
    except Exception as e:
        logger.error(f"=== ERROR in get_previews ===")
        logger.error(f"Error fetching previews: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to fetch previews: {str(e)}")


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


@router.post("/api/v1/previews/populate-all")
async def populate_all_previews(
    enterpriseName: Optional[str] = Query(None, description="Enterprise name (gamma, app, entelligence, beta). If not provided, uses default audience database.")
):
    """
    Populate previews for ALL audience rooms in the database.
    
    This endpoint:
    1. Ensures the preview table exists with the proper schema
    2. Fetches all audience rooms from the database based on enterprise name
    3. For each room, fetches data from S3 and creates/updates preview
    
    Query Parameters:
    - enterpriseName (optional): Enterprise name to determine which database to use:
        - "gamma" -> uses GAMMA_DATABASE_URL
        - "app" -> uses APP_DATABASE_URL
        - "entelligence" -> uses ENTELLIGENCE_DATABASE_URL
        - "beta" -> uses BETA_DATABASE_URL
        - If not provided, uses AUDIENCE_DATABASE_URL
    
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
        database.ensure_preview_table_exists(enterprise_name=enterpriseName)
        
        # Fetch all rooms with profiles (READ-ONLY)
        rooms = database.find_all_audience_rooms_with_profiles(limit=PREVIEW_PROFILE_LIMIT, enterprise_name=enterpriseName)
        
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
                    profiles=preview_data['profiles'],
                    enterprise_name=enterpriseName
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
        
        # Clean up orphaned and duplicate previews
        try:
            cleanup_results = database.delete_orphaned_previews(enterprise_name=enterpriseName)
            logger.info(f"Cleanup complete: Deleted {cleanup_results.get('orphaned', 0)} orphaned and {cleanup_results.get('duplicates', 0)} duplicate preview entries")
        except Exception as e:
            logger.error(f"Error deleting orphaned/duplicate previews: {e}", exc_info=True)
            # Don't fail the entire operation if cleanup fails
            cleanup_results = {"orphaned": 0, "duplicates": 0, "total": 0}
        
        return {
            "status": "success",
            "message": f"Populated previews for {successful} rooms",
            "total_rooms": len(rooms),
            "successful": successful,
            "failed": failed,
            "cleanup": {
                "orphaned_deleted": cleanup_results.get("orphaned", 0),
                "duplicates_deleted": cleanup_results.get("duplicates", 0),
                "total_deleted": cleanup_results.get("total", 0)
            },
            "rooms": results
        }
        
    except Exception as e:
        logger.error(f"Error populating all previews: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to populate previews: {str(e)}")


@router.patch("/api/v1/previews/update-name")
async def update_preview_name_endpoint(request: UpdatePreviewNameRequest):
    """
    Update the name of an audience room in the preview table.
    
    This endpoint updates only the name field in the previews table for the specified audience room.
    
    Request Body:
    - enterpriseName (optional): Enterprise name to determine which database to use:
        - "gamma" -> uses GAMMA_DATABASE_URL
        - "app" -> uses APP_DATABASE_URL
        - "entelligence" -> uses ENTELLIGENCE_DATABASE_URL
        - "beta" -> uses BETA_DATABASE_URL
        - If not provided, uses AUDIENCE_DATABASE_URL
    - newName (required): The new name to set for the audience room
    - audienceRoomId (required): The ID of the audience room to update
    
    Response includes:
    - status: Success status
    - room_id: The audience room ID that was updated
    - new_name: The new name that was set
    - enterpriseName: The enterprise name used (if any)
    
    WARNING: This only modifies the previews table, no other tables are touched.
    """
    logger.info(f"=== UPDATE PREVIEW NAME REQUEST START ===")
    logger.info(f"Request: room_id={request.audienceRoomId}, new_name={request.newName}, enterprise={request.enterpriseName}")
    
    ensure_db_available("audience")
    logger.info("Database availability check passed")
    
    try:
        logger.info(f"Updating preview name for room {request.audienceRoomId} (enterprise={request.enterpriseName})")
        updated_preview = database.update_preview_name(
            room_id=request.audienceRoomId,
            new_name=request.newName,
            enterprise_name=request.enterpriseName
        )
        
        if not updated_preview:
            logger.warning(f"Preview not found for room {request.audienceRoomId} (enterprise={request.enterpriseName})")
            raise HTTPException(
                status_code=404,
                detail=f"Preview not found for room {request.audienceRoomId}"
            )
        
        logger.info(f"Successfully updated preview name for room {request.audienceRoomId}")
        logger.info(f"=== UPDATE PREVIEW NAME REQUEST SUCCESS ===")
        
        return {
            "status": "success",
            "message": f"Preview name updated for room {request.audienceRoomId}",
            "room_id": request.audienceRoomId,
            "new_name": request.newName,
            "enterpriseName": request.enterpriseName
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"=== ERROR in update_preview_name ===")
        logger.error(f"Error updating preview name for room {request.audienceRoomId}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update preview name: {str(e)}"
        )


@router.delete("/api/v1/previews/{room_id}")
async def delete_preview(
    room_id: str = Path(..., description="The room ID to delete preview for"),
    user_id: Optional[str] = Query(None, description="Optional user ID to scope deletion"),
    enterpriseName: Optional[str] = Query(None, description="Enterprise name (gamma, app, entelligence, beta). If not provided, uses default audience database.")
):
    logger.info(f"=== DELETE PREVIEW REQUEST START ===")
    logger.info(f"Request: room_id={room_id}, user_id={user_id}, enterprise={enterpriseName}")
    """
    Delete a preview record.
    
    WARNING: This only deletes from the previews table, no other tables are touched.
    """
    ensure_db_available("audience")
    
    try:
        deleted = database.delete_preview(room_id, user_id, enterprise_name=enterpriseName)
        
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

