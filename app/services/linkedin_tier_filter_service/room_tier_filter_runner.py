"""Run tier-1 / tier-2 rule filters for an audience room against S3 tier JSON."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Literal, Optional

from app import database
from app.config import s3_bucket
from app.linkedin_tiered_s3 import get_linkedin_tiered_s3_artifact_names
from app.services.linkedin_tier_filter_service.filter_engine import (
    filter_tier1_authored_payload,
    filter_tier2_reposts_and_comments_payload,
)
from app.utils.s3_utils import get_audience_type_from_source, get_s3_key_for_audience, try_fetch_json_from_s3, upload_json_to_s3

logger = logging.getLogger(__name__)


def _tier_base_key(
    audience_room_id: str,
    enterprise_name: Optional[str],
    source: Optional[str],
) -> str:
    a = get_linkedin_tiered_s3_artifact_names()
    return get_s3_key_for_audience(
        audience_room_id, a.tiered_folder, enterprise_name, source
    ).rstrip("/")


def run_linkedin_tier_filters_for_room(
    audience_room_id: str,
    enterprise_name: Optional[str] = None,
    tier: Literal["1", "2", "both"] = "both",
) -> Optional[Dict[str, Any]]:
    """
    Read tier JSON from S3, apply rule filters, upload filtered artifacts (kept items only).

    tier: "1" = authored only, "2" = reposts/comments only, "both" = run 1 then 2.
    """
    if not database.is_audience_db_available():
        logger.warning("run_linkedin_tier_filters_for_room: audience DB unavailable")
        return None
    if not s3_bucket:
        logger.warning("run_linkedin_tier_filters_for_room: S3 bucket not configured")
        return None

    room = database.find_audience_room_by_id(
        audience_room_id, include_profiles=False, enterprise_name=enterprise_name
    )
    if not room:
        return None
    if get_audience_type_from_source(room.source) != "linkedin-audience":
        return {"skipped": True, "reason": "not_linkedin_audience", "source": room.source}

    art = get_linkedin_tiered_s3_artifact_names()
    base = _tier_base_key(audience_room_id, enterprise_name, room.source)
    out: Dict[str, Any] = {"audience_room_id": audience_room_id, "tier": tier}

    def _upload(name: str, payload: Dict[str, Any]) -> str:
        return upload_json_to_s3(f"{base}/{name}", payload)

    manifest = try_fetch_json_from_s3(f"{base}/{art.manifest}")
    if not isinstance(manifest, dict):
        manifest = {}
    manifest.setdefault("audience_room_id", audience_room_id)

    if tier in ("1", "both"):
        raw = try_fetch_json_from_s3(f"{base}/{art.tier_1_authored}") or {}
        kept, _dropped, stats = filter_tier1_authored_payload(
            raw, source_name=art.tier_1_authored
        )
        f1_url = _upload(art.tier_1_authored_filtered, kept)
        out["tier_1"] = {
            "stats": stats,
            "filtered_s3_url": f1_url,
        }
        manifest["tier_1_authored_filtered_posts"] = stats["kept"]
        manifest["tier_1_authored_filter_total_seen"] = stats["total_seen"]
        manifest["tier_1_authored_filter_dropped_count"] = stats["dropped"]
        manifest["tier_1_authored_filtered_s3_url"] = f1_url
        manifest.pop("tier_1_authored_filtered_dropped_s3_url", None)

    if tier in ("2", "both"):
        raw = try_fetch_json_from_s3(f"{base}/{art.tier_2_reposts_and_comments}") or {}
        kept, _dropped, stats = filter_tier2_reposts_and_comments_payload(
            raw, source_name=art.tier_2_reposts_and_comments
        )
        f2_url = _upload(art.tier_2_reposts_and_comments_filtered, kept)
        out["tier_2"] = {
            "stats": stats,
            "filtered_s3_url": f2_url,
        }
        manifest["tier_2_reposts_and_comments_filtered_interactions"] = stats["kept"]
        manifest["tier_2_reposts_and_comments_filter_total_seen"] = stats["total_seen"]
        manifest["tier_2_reposts_and_comments_filter_dropped_count"] = stats["dropped"]
        manifest["tier_2_reposts_and_comments_filtered_s3_url"] = f2_url
        manifest.pop("tier_2_reposts_and_comments_filtered_dropped_s3_url", None)

    manifest["tier_filter_generated_at"] = datetime.now(timezone.utc).isoformat()
    manifest_for_s3 = {k: v for k, v in manifest.items() if k != "manifest_s3_url"}
    manifest_url = _upload(art.manifest, manifest_for_s3)
    out.update(manifest_for_s3)
    out["manifest_s3_url"] = manifest_url

    logger.info(
        "run_linkedin_tier_filters_for_room: room=%s tier=%s keys_written",
        audience_room_id,
        tier,
    )
    return out
