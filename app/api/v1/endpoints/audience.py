"""Audience room endpoints."""
from typing import Any, Dict, Literal, Optional, cast
from fastapi import APIRouter, HTTPException, Path, Query

from app.config import logger
from app.models.schemas import CreateAudienceRoomRequest
from app.services.audience_room_creation_service import request_handler as audience_room_creation_handler
from app.services.audience_group_summarization_service import request_handler as audience_group_summarization_handler
from app.services.linkedin_post_tier_service import request_handler as linkedin_post_tier_handler
from app.services.linkedin_tier_filter_service import request_handler as linkedin_tier_filter_handler

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

    LinkedIn tier files (`tiered_posts/`) are not built by this endpoint; call
    `POST .../rebuild-tiered-posts` afterward when you want tiers on S3.

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


@router.post("/api/v1/audience-rooms/{audience_room_id}/rebuild-tiered-posts")
async def rebuild_tiered_posts(
    audience_room_id: str = Path(...),
    enterpriseName: Optional[str] = Query(
        None,
        description="Enterprise name (gamma, app, entelligence, beta). If not provided, uses default audience database.",
    ),
) -> Dict[str, Any]:
    """
    Separates posts and related activity into tiers and uploads JSON for the room to S3 (tiered_posts folder).

    S3 object basenames for unfiltered tier files: ``app/linkedin_tiered_s3/artifacts.yaml``.

    The tier-1 file stores posts the member actually wrote as the author: their own original
    feed content, not someone else's post body passed off as theirs.

    The tier-2 file stores higher-signal interaction with context: reposts and
    similar activity where they added substantive text, plus comments they left on other people's
    posts when the reply is long enough to be more than a throwaway line.

    tier_3_sparse.json stores the low-signal remainder: very short comments and thin repost
    chatter (for example CFBR, Interested, emoji-heavy or one-line replies) so those stay in the
    dataset without diluting tier 2.

    Before upload, authored post text and interaction stimulus and response strings are normalized
    in the service layer (emoji strip, fancy Unicode letters to plain ASCII, whitespace). There is
    no separate cleaning API; this endpoint is the single production step for tier files.

    manifest.json is also written with counts and URLs. Optional query parameter enterpriseName works like other audience-room routes.

    OpenAPI lists HTTP 422 for invalid request parameters (for example wrong types); a normal call with a valid room id and optional enterpriseName returns 200, not 422.
    """
    try:
        return await linkedin_post_tier_handler.rebuild_tiered_posts(
            audience_room_id=audience_room_id,
            enterprise_name=enterpriseName,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error rebuilding tiered posts: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to rebuild tiered posts: {str(e)}")


@router.post("/api/v1/audience-rooms/{audience_room_id}/filter-tiered-posts")
async def filter_tiered_posts(
    audience_room_id: str = Path(..., description="Audience room ID"),
    enterpriseName: Optional[str] = Query(
        None,
        description="Enterprise name (gamma, app, entelligence, beta). If not provided, uses default audience database.",
    ),
    tier: str = Query(
        "both",
        description="Limit which tier files are filtered in this call: 1 for authored posts only, 2 for reposts and comments only, or both.",
    ),
) -> Dict[str, Any]:
    """
    First filtering stage for LinkedIn tier data on S3: rule-based cleanup that cuts out irrelevant or
    low-signal items so downstream steps see stronger text. No LLM runs here.

    It removes or sidelines posts and replies that fail simple quality gates (too short, too many
    hashtags or mentions, emoji-heavy blocks, and common hiring or promo phrasing). Tier 1 applies
    those checks to authored post body text. Tier 2 applies them to the member reply text on reposts
    and comments.

    Call this after rebuild-tiered-posts so tier JSON already exists. Filtered JSON is written under
    the room tiered_posts folder, and manifest.json is updated with URLs and counts. The tier query
    must be 1, 2, or both.
    """
    if tier not in ("1", "2", "both"):
        raise HTTPException(
            status_code=400,
            detail="tier must be one of: 1, 2, both",
        )
    try:
        return await linkedin_tier_filter_handler.filter_tiered_posts(
            audience_room_id=audience_room_id,
            enterprise_name=enterpriseName,
            tier=cast(Literal["1", "2", "both"], tier),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error filtering tiered posts: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to filter tiered posts: {str(e)}")
