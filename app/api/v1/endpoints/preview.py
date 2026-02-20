"""Preview endpoints for audience room previews.

These endpoints manage the preview cache table for fast UI rendering.
Preview data includes room info, description summary, traits, and first 5 profiles.
"""
from typing import Optional
from fastapi import APIRouter, HTTPException, Query, Path
from app.config import logger
from app.services.audience_preview_update_service import request_handler as audience_preview_update_handler

router = APIRouter()


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
    try:
        return audience_preview_update_handler.update_preview_for_room(
            room_id=room_id,
            enterprise_name=enterpriseName,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating preview for room {room_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update preview: {str(e)}")

