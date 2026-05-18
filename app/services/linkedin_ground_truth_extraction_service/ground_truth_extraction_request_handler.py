"""HTTP-style entry: run ground-truth extraction for a LinkedIn audience room (S3), no stimulus coupling."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import HTTPException

from app import database
from app.config import s3_bucket, s3_client
from app.utils.helpers import ensure_db_available
from app.utils.s3_utils import get_audience_type_from_source

from .errors import GroundTruthExtractionError
from .ground_truth_extraction_room_runner import run_ground_truth_extraction_for_s3_artifacts


class GroundTruthExtractionRequestHandler:
    async def run_ground_truth_extraction(
        self,
        audience_room_id: str,
        enterprise_name: Optional[str] = None,
        model: Optional[str] = None,
        tiers: str = "both",
    ) -> Dict[str, Any]:
        ensure_db_available("audience")
        if not s3_client or not s3_bucket:
            raise HTTPException(
                status_code=503,
                detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.",
            )
        room = database.find_audience_room_by_id(
            audience_room_id, include_profiles=False, enterprise_name=enterprise_name
        )
        if not room:
            raise HTTPException(
                status_code=404, detail=f"Audience room {audience_room_id} not found"
            )
        if get_audience_type_from_source(room.source) != "linkedin-audience":
            return {
                "audience_room_id": audience_room_id,
                "skipped": True,
                "reason": "not_linkedin_audience",
                "source": room.source,
            }
        try:
            return await run_ground_truth_extraction_for_s3_artifacts(
                audience_room_id=audience_room_id,
                enterprise_name=enterprise_name,
                model=model,
                tier_mode=(tiers or "both").strip().lower(),
            )
        except GroundTruthExtractionError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e


request_handler = GroundTruthExtractionRequestHandler()
