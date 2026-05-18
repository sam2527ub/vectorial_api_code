"""
S3: filtered tier JSON + theme mapping → contextual stimulus + category output (tier-1, then tier-2).

Prerequisites: ``filter-tiered-posts`` and ``theme_category_discovery`` for the same room
(so discovered_category_topic_mapping_* files exist, unless tier-2 falls back to tier-1 list).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Dict, Optional

from app import database
from app.config import s3_bucket, s3_client
from app.linkedin_tiered_s3 import get_linkedin_tiered_s3_artifact_names
from app.utils.s3_utils import (
    get_audience_type_from_source,
    get_s3_key_for_audience,
    try_fetch_json_from_s3,
    upload_json_to_s3,
)
from .errors import StimulusExtractionError
from .stimulus_data import load_allowed_categories_from_mapping
from .stimulus_extraction_config import get_stimulus_extraction_config
from .stimulus_extraction_batch_processor import (
    run_tier1_stimulus_extraction,
    run_tier2_category_extraction,
)

log = logging.getLogger(__name__)


def _tier_base(
    audience_room_id: str, enterprise_name: Optional[str], source: Optional[str]
) -> str:
    a = get_linkedin_tiered_s3_artifact_names()
    return get_s3_key_for_audience(
        audience_room_id, a.tiered_folder, enterprise_name, source
    ).rstrip("/")


async def run_stimulus_categorization_for_room(
    audience_room_id: str,
    enterprise_name: Optional[str] = None,
    model: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if not database.is_audience_db_available():
        log.warning("run_stimulus_categorization_for_room: audience DB unavailable")
        return None
    if not s3_client or not s3_bucket:
        log.warning("run_stimulus_categorization_for_room: S3 not configured")
        return None

    room = database.find_audience_room_by_id(
        audience_room_id, include_profiles=False, enterprise_name=enterprise_name
    )
    if not room:
        return None
    if get_audience_type_from_source(room.source) != "linkedin-audience":
        return {
            "audience_room_id": audience_room_id,
            "skipped": True,
            "reason": "not_linkedin_audience",
            "source": room.source,
        }

    art = get_linkedin_tiered_s3_artifact_names()
    base = _tier_base(audience_room_id, enterprise_name, room.source)
    mdl = model or os.environ.get("OPENAI_MODEL") or get_stimulus_extraction_config().default_model

    map_t1 = try_fetch_json_from_s3(f"{base}/{art.discovered_category_topic_mapping_tier1_filtered}")
    if not map_t1 or not isinstance(map_t1, dict) or not map_t1:
        raise StimulusExtractionError(
            f"No {art.discovered_category_topic_mapping_tier1_filtered!r} on S3. Run theme_category_discovery (tier-1) first."
        )
    allowed_t1 = load_allowed_categories_from_mapping(map_t1)

    t1_filt = try_fetch_json_from_s3(f"{base}/{art.tier_1_authored_filtered}")
    if not t1_filt or not isinstance(t1_filt, dict):
        raise StimulusExtractionError(
            f"No {art.tier_1_authored_filtered!r} on S3. Run filter-tiered-posts first."
        )

    t1_out = await run_tier1_stimulus_extraction(
        tier_data=t1_filt,
        source_file=art.tier_1_authored_filtered,
        room_id=audience_room_id,
        allowed_categories=allowed_t1,
        model=mdl,
    )
    t1_key = f"{base}/{art.contextual_stimulus_extraction_tier1_filtered}"
    t1_url = upload_json_to_s3(t1_key, t1_out)

    map_t2 = try_fetch_json_from_s3(f"{base}/{art.discovered_category_topic_mapping_tier2_filtered}")
    if map_t2 and isinstance(map_t2, dict) and map_t2.get("categories"):
        try:
            allowed_t2 = load_allowed_categories_from_mapping(map_t2)
        except StimulusExtractionError:
            allowed_t2 = allowed_t1
            log.warning("Tier-2 mapping present but failed to parse categories; using tier-1 list")
    else:
        log.info(
            "No usable %s; using tier-1 category list for tier-2 replies (same as script fallback)",
            art.discovered_category_topic_mapping_tier2_filtered,
        )
        allowed_t2 = allowed_t1

    t2_filt = try_fetch_json_from_s3(f"{base}/{art.tier_2_reposts_and_comments_filtered}")
    if not t2_filt or not isinstance(t2_filt, dict):
        raise StimulusExtractionError(
            f"No {art.tier_2_reposts_and_comments_filtered!r} on S3. Run filter-tiered-posts (tier-2) first."
        )
    t2_out = await run_tier2_category_extraction(
        tier_data=t2_filt,
        source_file=art.tier_2_reposts_and_comments_filtered,
        room_id=audience_room_id,
        allowed_categories=allowed_t2,
        model=mdl,
    )
    t2_key = f"{base}/{art.contextual_stimulus_extraction_tier2_filtered}"
    t2_url = upload_json_to_s3(t2_key, t2_out)

    mpath = f"{base}/{art.manifest}"
    man: Dict[str, Any] = try_fetch_json_from_s3(mpath) or {}
    if isinstance(man, dict):
        man["contextual_stimulus_extraction_tier1_s3_url"] = t1_url
        man["contextual_stimulus_extraction_tier2_s3_url"] = t2_url
        man["stimulus_categorization_last_run_at"] = datetime.now(timezone.utc).isoformat()
        man_for_s3 = {k: v for k, v in man.items() if k != "manifest_s3_url"}
        upload_json_to_s3(mpath, man_for_s3)
    return {
        "audience_room_id": audience_room_id,
        "mode": "both",
        "tier1": {
            "mapping_s3_key": t1_key,
            "mapping_s3_url": t1_url,
            "total_posts_processed": t1_out.get("total_posts_processed"),
        },
        "tier2": {
            "mapping_s3_key": t2_key,
            "mapping_s3_url": t2_url,
            "total_posts_processed": t2_out.get("total_posts_processed"),
        },
        "model": mdl,
    }


__all__ = ("run_stimulus_categorization_for_room",)
