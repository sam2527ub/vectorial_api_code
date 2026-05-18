"""HTTP: contextual stimulus (tier-1) + per-reply category (tier-2) for LinkedIn rooms (S3)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import HTTPException

from app import database
from app.config import logger, s3_bucket, s3_client
from app.utils.helpers import ensure_db_available
from app.utils.s3_utils import get_audience_type_from_source

from .errors import StimulusExtractionError
from .stimulus_extraction_room_runner import run_stimulus_categorization_for_room


class StimulusExtractionRequestHandler:
    async def run_contextual_stimulus_categorization(
        self,
        audience_room_id: str,
        enterprise_name: Optional[str] = None,
        model: Optional[str] = None,
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
            result = await run_stimulus_categorization_for_room(
                audience_room_id=audience_room_id,
                enterprise_name=enterprise_name,
                model=model,
            )
        except StimulusExtractionError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                "stimulus_categorization failed for room %s: %s",
                audience_room_id,
                e,
                exc_info=True,
            )
            raise HTTPException(
                status_code=500, detail=f"Stimulus categorization failed: {str(e)}"
            ) from e

        if result is None:
            raise HTTPException(
                status_code=500,
                detail="Stimulus categorization returned no result; check S3 and logs.",
            )
        if result.get("skipped"):
            return result
        return {
            "audience_room_id": audience_room_id,
            "status": "ok",
            **{k: v for k, v in result.items() if k != "audience_room_id"},
        }


request_handler = StimulusExtractionRequestHandler()
