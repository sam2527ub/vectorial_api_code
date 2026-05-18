"""HTTP entry: theme taxonomy (category/topic) discovery for LinkedIn rooms via S3 + pipeline."""

from typing import Any, Dict, Optional

from fastapi import HTTPException

from app import database
from app.config import logger, s3_bucket, s3_client
from app.utils.helpers import ensure_db_available
from app.utils.s3_utils import get_audience_type_from_source

from .pipeline import ThemeDiscoveryPipelineError
from .room_theme_discovery_runner import run_linkedin_theme_discovery_both_tiers_for_room


class LinkedinThemeDiscoveryRequestHandler:
    async def theme_category_discovery(
        self,
        audience_room_id: str,
        enterprise_name: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Run MAP/SHRINK/CATEGORIZE for **tier 1** (authored) then **tier 2** (replies/interactions) in order;
        each writes its discovered JSON on S3. Object basenames: ``app/linkedin_tiered_s3/artifacts.yaml``.
        """
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

        try:
            result = await run_linkedin_theme_discovery_both_tiers_for_room(
                audience_room_id=audience_room_id,
                enterprise_name=enterprise_name,
                model=model,
            )
        except ThemeDiscoveryPipelineError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                "theme_category_discovery failed for room %s: %s",
                audience_room_id,
                e,
                exc_info=True,
            )
            raise HTTPException(
                status_code=500, detail=f"Theme discovery failed: {str(e)}"
            ) from e

        if result is None:
            raise HTTPException(
                status_code=500,
                detail="Theme discovery returned no result (DB or S3 not available; check logs).",
            )
        if result.get("skipped"):
            return result
        return {
            "audience_room_id": audience_room_id,
            "status": "ok",
            **{k: v for k, v in result.items() if k != "audience_room_id"},
        }


request_handler = LinkedinThemeDiscoveryRequestHandler()
