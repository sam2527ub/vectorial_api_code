"""Preview endpoints."""
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from app.config import logger
from app.utils.helpers import ensure_db_available
from app import database

router = APIRouter()


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
    - description_summary: Summary from room description (if available)
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
    - description_summary: Summary from room description (if available)
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

