"""Audience room endpoints."""
import sys
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException, Path, Query
from pydantic import BaseModel, Field
from app.api.schemas import (
    CreateRedditAudienceRoomRequest,
    CreateRedditAudienceRoomShellRequest,
    AudienceRoomStatusUpdateRequest,
)
from app.config import logger
from app.utils.helpers import ensure_db_available
from app.services.audience_room_creation_service import request_handler as creation_handler
from app.services.user_profile_summarization_service import job_handler
from app.services.audience_group_summarization_service import request_handler as group_summary_handler
from app.services.comment_context_service import CommentContextStartHandler, CommentContextStatusHandler
from app.services.comment_context_summary_service import CommentContextSummaryHandler
from app.services.audience_room_creation_service.utils.s3_storage_manager import S3Manager
from app.utils.s3_utils import get_s3_key_for_audience, upload_json_to_s3
from app.database.repositories.shared_repositories import (
    find_audience_room_by_id,
    upsert_audience_room_with_profiles,
    set_audience_room_status,
)

router = APIRouter()


# CommentContextSummaryRequest removed - now using audience_room_id from path


def flush_logs():
    """Force flush stdout/stderr to ensure logs appear immediately."""
    sys.stdout.flush()
    sys.stderr.flush()


@router.post("/api/v1/audience-rooms/reddit")
async def create_reddit_audience_room(payload: CreateRedditAudienceRoomRequest):
    """Create a Reddit audience room with profiles, S3 storage, and Postgres metadata."""
    logger.info("=" * 80)
    logger.info("REQUEST RECEIVED: POST /api/v1/audience-rooms/reddit")
    logger.info(f"Request Details:")
    logger.info(f"  - Audience Room Name: {payload.audience_room_name}")
    logger.info(f"  - Enterprise Name: {payload.enterpriseName}")
    logger.info(f"  - User ID: {payload.userId}")
    logger.info(f"  - Source: {payload.source}")
    logger.info(f"  - Query: {payload.query}")
    logger.info(f"  - Users Count: {len(payload.users)}")
    logger.info(f"  - Search Results Count: {len(payload.search_results)}")
    logger.info(f"  - Activities Count: {len(payload.activities) if payload.activities else 0}")
    logger.info(f"  - Activities S3 URL: {payload.activities_s3_url}")
    logger.info(f"  - Category: {payload.category}")
    logger.info(f"  - Subreddit Similarity Data: {'Provided' if payload.subreddit_similarity_data else 'Not provided'}")
    logger.info("=" * 80)
    flush_logs()
    
    ensure_db_available("audience")
    
    try:
        from app.services.audience_room_creation_service.schemas import AudienceRoomCreationRequest
        
        service_payload = AudienceRoomCreationRequest(
            audience_room_name=payload.audience_room_name,
            enterpriseName=payload.enterpriseName,
            userId=payload.userId,
            source=payload.source,
            query=payload.query,
            users=payload.users,
            search_results=payload.search_results,
            activities=payload.activities,
            activities_s3_url=payload.activities_s3_url,
            audience_room_description=payload.audience_room_description,
            category=payload.category,
            subreddit_similarity_data=payload.subreddit_similarity_data,
            audience_room_id=payload.audience_room_id,
            is_preview=payload.is_preview,
            is_preview_then_real=payload.is_preview_then_real,
        )
        
        response = await creation_handler.handle_request(service_payload)
        
        logger.info("RESPONSE SENT: POST /api/v1/audience-rooms/reddit")
        logger.info(f"Response Details:")
        logger.info(f"  - Audience Room ID: {response.get('audience_room_id')}")
        logger.info(f"  - Audience Room Name: {response.get('audience_room_name')}")
        logger.info(f"  - Profiles Created: {response.get('profiles_created')}")
        logger.info("=" * 80)
        flush_logs()
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating audience room: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Error creating Reddit audience room: {str(e)}"
        )


# ---------------------------------------------------------------------------
# Preview-then-Real workflow: shell + status endpoints
# ---------------------------------------------------------------------------

@router.post("/api/v1/audience-rooms/reddit/shell", status_code=201)
async def create_reddit_audience_room_shell(payload: CreateRedditAudienceRoomShellRequest):
    """
    Pre-allocate a stable AudienceRoom row for the Preview-then-Real Vercel
    Workflow. No profiles or activities are attached; the row is marked with
    status=CREATING and isPreview=true so downstream services (chat agent,
    analytics) can tell an in-flight room from a ready one without breaking
    on missing FKs.

    Idempotent: calling this with the same audience_room_id again returns the
    existing row.
    """
    logger.info(
        "=== CREATE REDDIT AUDIENCE ROOM SHELL === id=%s name=%s enterprise=%s",
        payload.audience_room_id, payload.audience_room_name, payload.enterpriseName,
    )
    try:
        ensure_db_available("audience")

        existing = find_audience_room_by_id(
            payload.audience_room_id,
            enterprise_name=payload.enterpriseName,
        )
        if existing:
            logger.info(f"Shell room already exists: {payload.audience_room_id}")
            return {
                "audience_room_id": existing.id,
                "audience_room_name": existing.name,
                "status": existing.status or "CREATING",
                "isPreview": True if existing.isPreview is None else existing.isPreview,
                "existed": True,
            }

        s3_manager = S3Manager()
        s3_manager.ensure_enterprise_folders(payload.enterpriseName or "default")
        description_url = s3_manager.upload_room_description(
            room_id=payload.audience_room_id,
            audience_room_name=payload.audience_room_name,
            audience_room_description=payload.audience_room_description,
            enterprise_name=payload.enterpriseName or "default",
            source=payload.source or "Reddit",
        )

        room = upsert_audience_room_with_profiles(
            room_id=payload.audience_room_id,
            name=payload.audience_room_name,
            description_s3_url=description_url,
            user_id=payload.userId,
            source=payload.source or "Reddit",
            query=payload.query,
            indexes_s3_url=None,
            category=payload.category,
            profiles_data=None,
            enterprise_name=payload.enterpriseName,
            replace_profiles=False,
        )

        try:
            set_audience_room_status(
                room_id=room.id,
                status="CREATING",
                is_preview=True,
                enterprise_name=payload.enterpriseName,
            )
        except Exception as e:
            logger.warning(
                "Could not set status/isPreview on %s (migration applied?): %s",
                room.id, e,
            )

        return {
            "audience_room_id": room.id,
            "audience_room_name": room.name,
            "description_s3_url": room.descriptionS3Url,
            "userId": room.userId,
            "status": "CREATING",
            "isPreview": True,
            "existed": False,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create Reddit audience room shell: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to create shell: {str(e)}")


@router.post("/api/v1/audience-rooms/{audience_room_id}/status")
async def update_reddit_audience_room_status(
    payload: AudienceRoomStatusUpdateRequest,
    audience_room_id: str = Path(...),
):
    """
    Mutate preview-workflow tracking columns on an AudienceRoom. Used by the
    markUpgrading / markPreviewReady / finalize workflow helpers. All fields
    optional so a single shape can drive each flip.
    """
    logger.info(
        "=== UPDATE AUDIENCE ROOM STATUS === id=%s status=%s isPreview=%s "
        "markPreviewReady=%s markFullReady=%s",
        audience_room_id, payload.status, payload.isPreview,
        payload.markPreviewReady, payload.markFullReady,
    )
    try:
        ensure_db_available("audience")
        now = datetime.utcnow()
        updated = set_audience_room_status(
            room_id=audience_room_id,
            status=payload.status,
            is_preview=payload.isPreview,
            preview_ready_at=now if payload.markPreviewReady else None,
            full_ready_at=now if payload.markFullReady else None,
            enterprise_name=payload.enterpriseName,
        )
        if not updated:
            raise HTTPException(
                status_code=404,
                detail=f"AudienceRoom {audience_room_id} not found (or status columns missing)",
            )
        return {
            "audience_room_id": updated.id,
            "status": payload.status,
            "isPreview": payload.isPreview,
            "previewReadyAt": now.isoformat() if payload.markPreviewReady else None,
            "fullReadyAt": now.isoformat() if payload.markFullReady else None,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update audience room status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to update status: {str(e)}")


class SummaryStatusRequest(BaseModel):
    """Request schema for checking summary generation status."""
    job_id: str = Field(..., description="Job ID returned from start endpoint")



@router.post("/api/v1/audience-rooms/{audience_room_id}/generate-summaries/start")
async def start_generate_summaries(
    audience_room_id: str = Path(...),
    enterpriseName: Optional[str] = Query(None, description="Enterprise name (gamma, app, entelligence, beta). If not provided, uses default audience database.")
):
    """Start chunked summary generation and return job_id for polling."""
    logger.info("=" * 80)
    logger.info("REQUEST RECEIVED: POST /api/v1/audience-rooms/{audience_room_id}/generate-summaries/start")
    logger.info(f"Request Details:")
    logger.info(f"  - Audience Room ID: {audience_room_id}")
    logger.info(f"  - Enterprise Name: {enterpriseName}")
    logger.info("=" * 80)
    
    ensure_db_available("audience")
    from app.services.user_profile_summarization_service.clients.storage.factory import get_storage_client
    from app.services.ai_gateway_service import ai_gateway
    if not ai_gateway.enabled:
        raise HTTPException(status_code=503, detail="AI Gateway not enabled. Please configure AI_GATEWAY_API_KEY or OPENAI_API_KEY.")
    storage_client = get_storage_client()
    if not storage_client or not storage_client.is_configured() or not storage_client.get_bucket_name():
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")
    
    try:
        response = job_handler.start_job(audience_room_id, enterpriseName)
        
        logger.info("RESPONSE SENT: POST /api/v1/audience-rooms/{audience_room_id}/generate-summaries/start")
        logger.info(f"Response Details:")
        logger.info(f"  - Job ID: {response['job_id']}")
        logger.info(f"  - Audience Room ID: {response['audience_room_id']}")
        logger.info(f"  - Status: {response['status']}")
        logger.info(f"  - Total Profiles: {response['total_profiles']}")
        logger.info("=" * 80)
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting summary generation: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to start summary generation: {str(e)}")


@router.post("/api/v1/audience-rooms/{audience_room_id}/generate-summaries/status")
async def get_summary_status(
    payload: SummaryStatusRequest,
    audience_room_id: str = Path(...),
    enterpriseName: Optional[str] = Query(None, description="Enterprise name (gamma, app, entelligence, beta). If not provided, uses default audience database.")
):
    """Check summary generation status and process next batch if running."""
    logger.info("=" * 80)
    logger.info("REQUEST RECEIVED: POST /api/v1/audience-rooms/{audience_room_id}/generate-summaries/status")
    logger.info(f"Request Details:")
    logger.info(f"  - Audience Room ID: {audience_room_id}")
    logger.info(f"  - Job ID: {payload.job_id}")
    logger.info(f"  - Enterprise Name: {enterpriseName}")
    logger.info("=" * 80)
    
    try:
        response = await job_handler.check_status(
            payload.job_id,
            audience_room_id,
            enterpriseName
        )
        
        logger.info("RESPONSE SENT: POST /api/v1/audience-rooms/{audience_room_id}/generate-summaries/status")
        logger.info(f"Response Details:")
        logger.info(f"  - Status: {response['status']}")
        if 'processed_profiles' in response:
            logger.info(f"  - Progress: {response['processed_profiles']}/{response['total_profiles']} profiles")
            logger.info(f"  - Success: {response['success_count']}, Skipped: {response['skipped_count']}, Errors: {response['error_count']}")
        logger.info("=" * 80)
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error checking summary status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to check summary status: {str(e)}")


@router.post("/api/v1/audience-rooms/{audience_room_id}/generate-group-summary")
async def generate_group_summary(
    audience_room_id: str = Path(...),
    enterpriseName: Optional[str] = Query(None, description="Enterprise name (gamma, app, entelligence, beta). If not provided, uses default audience database."),
    use_posts_and_about: bool = Query(
        False,
        description=(
            "Preview-then-Real flag. When true, per-profile summaries are built "
            "from scraped posts.json + comments.json directly instead of requiring "
            "LLM profile summaries. Reddit has no 'about' equivalent, so activity "
            "text only; the flag name matches the LinkedIn API for parity."
        ),
    ),
):
    """Generate group summary and traits for a Reddit audience room."""
    logger.info("=" * 80)
    logger.info("REQUEST RECEIVED: POST /api/v1/audience-rooms/{audience_room_id}/generate-group-summary")
    logger.info(f"Request Details:")
    logger.info(f"  - Audience Room ID: {audience_room_id}")
    logger.info(f"  - Enterprise Name: {enterpriseName}")
    logger.info(f"  - use_posts_and_about: {use_posts_and_about}")
    logger.info("=" * 80)
    
    ensure_db_available("audience")
    
    try:
        response = await group_summary_handler.handle_request(
            audience_room_id=audience_room_id,
            enterprise_name=enterpriseName,
            use_posts_and_about=use_posts_and_about,
        )
        
        logger.info("RESPONSE SENT: POST /api/v1/audience-rooms/{audience_room_id}/generate-group-summary")
        logger.info(f"Response Details:")
        logger.info(f"  - Audience Room ID: {response['audience_room_id']}")
        logger.info(f"  - Audience Room Name: {response['audience_room_name']}")
        logger.info(f"  - Total Profiles: {response['total_profiles']}")
        logger.info(f"  - Profiles Processed: {response['profiles_processed']}")
        logger.info(f"  - Profiles Skipped: {response['profiles_skipped']}")
        logger.info(f"  - Traits Count: {len(response['traits'])}")
        logger.info("=" * 80)
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating group summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to generate group summary: {str(e)}")


# ----- Service 1: Comment context (scraping) - start + status, store in S3 -----


@router.post("/api/v1/audience-rooms/{audience_room_id}/comment-context/start")
async def comment_context_start(
    audience_room_id: str = Path(...),
    enterpriseName: Optional[str] = Query(None, description="Enterprise name"),
):
    """Start Apify runs for post URLs. Poll status endpoint until scraping_complete."""
    logger.info("REQUEST RECEIVED: POST .../comment-context/start audience_room_id=%s", audience_room_id)
    ensure_db_available("audience")
    try:
        handler = CommentContextStartHandler()
        return await handler.start_job(audience_room_id, enterpriseName or "default")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting comment context job: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/v1/audience-rooms/{audience_room_id}/comment-context/status")
async def comment_context_status(
    audience_room_id: str = Path(...),
    enterpriseName: Optional[str] = Query(None, description="Enterprise name"),
):
    """Poll Apify runs; fetch and store completed run results in S3. Call until status=scraping_complete."""
    logger.info("REQUEST RECEIVED: GET .../comment-context/status audience_room_id=%s", audience_room_id)
    try:
        handler = CommentContextStatusHandler()
        return await handler.check_status(audience_room_id, enterpriseName or "default")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error checking comment context status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ----- Service 2: Comment context summary - load S3, add context + Groq to comments.json -----


# @router.post("/api/v1/audience-rooms/{audience_room_id}/comment-context-summary")
# async def comment_context_summary(
#     audience_room_id: str = Path(...),
#     enterpriseName: Optional[str] = Query(None, description="Enterprise name"),
# ):
#     """Load scraped data from S3 (using audience_room_id), add context + Groq summary to each profile's comments.json."""
#     logger.info("REQUEST RECEIVED: POST .../comment-context-summary audience_room_id=%s", audience_room_id)
#     ensure_db_available("audience")
#     try:
#         handler = CommentContextSummaryHandler()
#         return await handler.handle_request(
#             audience_room_id,
#             enterpriseName or "default",
#         )
#     except HTTPException:
#         raise
#     except Exception as e:
#         logger.error(f"Error in comment context summary: {e}", exc_info=True)
#         raise HTTPException(status_code=500, detail=str(e))


class CreationAuditUploadBody(BaseModel):
    """Observability payload from the reddit audience creation workflow."""

    manifest: Dict[str, Any] = Field(default_factory=dict)
    report: Dict[str, Any] = Field(default_factory=dict)


@router.post("/api/v1/audience-rooms/{audience_room_id}/creation-audit")
async def upload_creation_audit(
    audience_room_id: str = Path(..., description="Audience room id"),
    enterpriseName: Optional[str] = Query(None, description="Enterprise name (e.g. gamma, app)"),
    payload: CreationAuditUploadBody = Body(...),
):
    """
    Persist creation funnel manifest + report JSON to S3 under
    {enterprise}/reddit-audience/{audience_room_id}/creation-audit/.
    """
    manager = S3Manager()
    if not manager.storage_client.is_configured():
        raise HTTPException(status_code=503, detail="S3 is not configured for creation audit")

    ent = (enterpriseName or "default").strip() or "default"
    source = "reddit"

    manifest_key = get_s3_key_for_audience(
        audience_room_id, "creation-audit/manifest.json", ent, source
    )
    report_key = get_s3_key_for_audience(
        audience_room_id, "creation-audit/report.json", ent, source
    )

    try:
        manifest_url = upload_json_to_s3(
            manifest_key,
            payload.manifest,
            s3_client=manager.storage_client.get_raw_client(),
            s3_bucket=manager.storage_client.get_bucket_name(),
            s3_region=manager.storage_client.get_region(),
        )
        report_url = upload_json_to_s3(
            report_key,
            payload.report,
            s3_client=manager.storage_client.get_raw_client(),
            s3_bucket=manager.storage_client.get_bucket_name(),
            s3_region=manager.storage_client.get_region(),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("creation-audit S3 upload failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to persist creation audit") from e

    logger.info(
        "creation-audit stored audience_room_id=%s enterprise=%s",
        audience_room_id,
        ent,
    )
    return {
        "audience_room_id": audience_room_id,
        "manifest_s3_key": manifest_key,
        "report_s3_key": report_key,
        "manifest_url": manifest_url,
        "report_url": report_url,
    }

