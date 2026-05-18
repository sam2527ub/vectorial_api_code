"""HTTP entry for the first rule-based filtering stage on LinkedIn tier JSON (no LLM)."""

from typing import Any, Dict, Literal, Optional

from fastapi import HTTPException

from app import database
from app.config import logger, s3_bucket, s3_client
from app.services.linkedin_tier_filter_service.room_tier_filter_runner import (
    run_linkedin_tier_filters_for_room,
)
from app.utils.helpers import ensure_db_available
from app.utils.s3_utils import get_audience_type_from_source


class LinkedinTierFilterRequestHandler:
    async def filter_tiered_posts(
        self,
        audience_room_id: str,
        enterprise_name: Optional[str] = None,
        tier: Literal["1", "2", "both"] = "both",
    ) -> Dict[str, Any]:
        ensure_db_available("audience")
        if not s3_client or not s3_bucket:
            raise HTTPException(
                status_code=503,
                detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.",
            )

        room = database.find_audience_room_by_id(
            audience_room_id,
            include_profiles=False,
            enterprise_name=enterprise_name,
        )
        if not room:
            raise HTTPException(status_code=404, detail=f"Audience room {audience_room_id} not found")
        if get_audience_type_from_source(room.source) != "linkedin-audience":
            return {
                "audience_room_id": audience_room_id,
                "skipped": True,
                "reason": "not_linkedin_audience",
                "source": room.source,
            }

        try:
            result = run_linkedin_tier_filters_for_room(
                audience_room_id=audience_room_id,
                enterprise_name=enterprise_name,
                tier=tier,
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error("filter_tiered_posts failed for room %s: %s", audience_room_id, e, exc_info=True)
            raise HTTPException(status_code=500, detail=f"Tier filter failed: {str(e)}") from e

        if result is None:
            raise HTTPException(status_code=500, detail="Tier filter returned no result (check logs).")
        if result.get("skipped"):
            return result
        return {
            "audience_room_id": audience_room_id,
            "status": "ok",
            **{k: v for k, v in result.items() if k != "audience_room_id"},
        }


request_handler = LinkedinTierFilterRequestHandler()
