"""HTTP-oriented entry points for LinkedIn tiered post rebuild (no LLM)."""

from typing import Any, Dict, Optional

from fastapi import HTTPException

from app import database
from app.config import logger, s3_bucket, s3_client
from app.services.linkedin_post_tier_service.tier_builder import rebuild_linkedin_post_tiers_for_room
from app.utils.helpers import ensure_db_available
from app.utils.s3_utils import get_audience_type_from_source


class LinkedinPostTierRequestHandler:
    """Rebuild tier_1/2/3 JSON in S3 from existing profile posts/comments (LinkedIn rooms only)."""

    async def rebuild_tiered_posts(
        self,
        audience_room_id: str,
        enterprise_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Idempotent S3-only rebuild. Does not call group summary or any LLM.

        Raises HTTPException for client/server errors; returns a skipped payload
        for non-LinkedIn audience types (200).
        """
        ensure_db_available("audience")
        if not s3_client or not s3_bucket:
            raise HTTPException(
                status_code=503,
                detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.",
            )

        room = database.find_audience_room_by_id(
            audience_room_id,
            include_profiles=True,
            enterprise_name=enterprise_name,
        )
        if not room:
            raise HTTPException(
                status_code=404,
                detail=f"Audience room {audience_room_id} not found",
            )
        if get_audience_type_from_source(room.source) != "linkedin-audience":
            return {
                "audience_room_id": audience_room_id,
                "skipped": True,
                "reason": "not_linkedin_audience",
                "source": room.source,
            }
        if not room.profiles:
            raise HTTPException(
                status_code=400,
                detail=f"No profiles in audience room {audience_room_id}",
            )

        try:
            manifest = rebuild_linkedin_post_tiers_for_room(
                audience_room_id=audience_room_id,
                enterprise_name=enterprise_name,
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                "rebuild_tiered_posts failed for room %s: %s",
                audience_room_id,
                e,
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail=f"Tier rebuild failed: {str(e)}",
            ) from e

        if manifest is None:
            raise HTTPException(
                status_code=500,
                detail="Tier rebuild returned no result (check server logs).",
            )
        if manifest.get("skipped"):
            return manifest

        return {
            "audience_room_id": audience_room_id,
            "status": "ok",
            **manifest,
        }


request_handler = LinkedinPostTierRequestHandler()
