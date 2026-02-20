"""Audience room endpoints."""
from typing import Optional
from fastapi import APIRouter, HTTPException, Path, Query

from app.config import logger
from app.models.schemas import CreateAudienceRoomRequest
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