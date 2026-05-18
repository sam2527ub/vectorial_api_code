"""S3: theme mapping + contextual stimulus → ground-truth topic labels (tier-1 and tier-2, independent)."""

from __future__ import annotations

import copy
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app import database
from app.config import s3_bucket, s3_client
from app.linkedin_tiered_s3 import LinkedinTieredS3ArtifactNames, get_linkedin_tiered_s3_artifact_names
from app.services.linkedin_stimulus_extraction_service.stimulus_data import load_allowed_categories_from_mapping
from app.utils.s3_utils import get_audience_type_from_source, try_fetch_json_from_s3, upload_json_to_s3

from .errors import GroundTruthExtractionError
from .ground_truth_extraction_config import get_ground_truth_extraction_config
from .ground_truth_extraction_processor import (
    run_ground_truth_extraction_on_payload,
    topics_mapping_from_mapping_object,
)

log = logging.getLogger(__name__)


def _resolve_tier2_mapping(
    map_t2: Optional[Dict[str, Any]],
    map_t1: Dict[str, Any],
) -> Dict[str, Any]:
    if map_t2 and isinstance(map_t2, dict) and map_t2.get("categories"):
        try:
            load_allowed_categories_from_mapping(map_t2)
            return map_t2
        except Exception:
            log.warning("Tier-2 category mapping present but invalid; using tier-1 topic lists")
    log.info("Using tier-1 category→topic mapping for tier-2 (tier-2 mapping missing or empty)")
    return map_t1


def _tier_base(
    audience_room_id: str, enterprise_name: Optional[str], source: Optional[str]
) -> str:
    from app.utils.s3_utils import get_s3_key_for_audience

    a = get_linkedin_tiered_s3_artifact_names()
    return get_s3_key_for_audience(
        audience_room_id, a.tiered_folder, enterprise_name, source
    ).rstrip("/")


async def run_ground_truth_extraction_for_s3_artifacts(
    audience_room_id: str,
    enterprise_name: Optional[str] = None,
    model: Optional[str] = None,
    *,
    t1_stimulus: Optional[Dict[str, Any]] = None,
    t2_stimulus: Optional[Dict[str, Any]] = None,
    map_t1: Optional[Dict[str, Any]] = None,
    map_t2: Optional[Dict[str, Any]] = None,
    art: Optional[LinkedinTieredS3ArtifactNames] = None,
    tier_mode: str = "both",
) -> Dict[str, Any]:
    """
    Read stimulus JSON + category→topic mapping from S3 (or use in-memory dicts), run ground-truth
    extraction, write ``ground_truth_extraction_tier{1,2}_filtered`` and update manifest.
    """
    if not s3_client or not s3_bucket:
        raise GroundTruthExtractionError("S3 is not configured")

    room = database.find_audience_room_by_id(
        audience_room_id, include_profiles=False, enterprise_name=enterprise_name
    )
    if not room:
        raise GroundTruthExtractionError(f"Audience room {audience_room_id!r} not found")
    if get_audience_type_from_source(room.source) != "linkedin-audience":
        return {
            "audience_room_id": audience_room_id,
            "skipped": True,
            "reason": "not_linkedin_audience",
            "source": room.source,
        }

    names = art or get_linkedin_tiered_s3_artifact_names()
    base = _tier_base(audience_room_id, enterprise_name, room.source)
    cfg = get_ground_truth_extraction_config()
    mdl = model or os.environ.get("OPENAI_MODEL", cfg.default_model)
    mode = (tier_mode or "both").strip().lower()
    if mode not in ("both", "tier1", "tier2"):
        mode = "both"

    m1 = map_t1
    if m1 is None:
        m1 = try_fetch_json_from_s3(f"{base}/{names.discovered_category_topic_mapping_tier1_filtered}")
    if not m1 or not isinstance(m1, dict):
        raise GroundTruthExtractionError(
            f"No {names.discovered_category_topic_mapping_tier1_filtered!r} on S3. Run theme_category_discovery first."
        )
    topics_t1 = topics_mapping_from_mapping_object(m1)

    topics_t2: Dict[str, List[str]]
    if mode == "tier1":
        topics_t2 = {}
    else:
        m2: Optional[Dict[str, Any]] = map_t2
        if m2 is None:
            m2 = try_fetch_json_from_s3(
                f"{base}/{names.discovered_category_topic_mapping_tier2_filtered}"
            )
        m2 = _resolve_tier2_mapping(m2 if isinstance(m2, dict) else None, m1)
        topics_t2 = topics_mapping_from_mapping_object(m2)

    out: Dict[str, Any] = {
        "audience_room_id": audience_room_id,
        "model": mdl,
        "tier_mode": mode,
        "config": cfg.to_public_dict(),
    }
    mpath = f"{base}/{names.manifest}"
    man: Dict[str, Any] = try_fetch_json_from_s3(mpath) or {}
    if not isinstance(man, dict):
        man = {}

    if mode in ("both", "tier1"):
        t1 = t1_stimulus
        if t1 is None:
            t1 = try_fetch_json_from_s3(f"{base}/{names.contextual_stimulus_extraction_tier1_filtered}")
        if not t1 or not isinstance(t1, dict):
            raise GroundTruthExtractionError(
                f"No {names.contextual_stimulus_extraction_tier1_filtered!r} on S3. Run stimulus categorization (tier-1) first."
            )
        t1w = copy.deepcopy(t1)
        meta1 = await run_ground_truth_extraction_on_payload(
            t1w, topics_t1, mdl, is_tier2=False, config=cfg
        )
        k1 = f"{base}/{names.ground_truth_extraction_tier1_filtered}"
        u1 = upload_json_to_s3(k1, t1w)
        out["tier1"] = {"s3_key": k1, "s3_url": u1, "meta": meta1}
        man["ground_truth_extraction_tier1_s3_url"] = u1
        man["ground_truth_extraction_tier1_at"] = datetime.now(timezone.utc).isoformat()

    if mode in ("both", "tier2"):
        t2 = t2_stimulus
        if t2 is None:
            t2 = try_fetch_json_from_s3(f"{base}/{names.contextual_stimulus_extraction_tier2_filtered}")
        if not t2 or not isinstance(t2, dict):
            raise GroundTruthExtractionError(
                f"No {names.contextual_stimulus_extraction_tier2_filtered!r} on S3. Run stimulus categorization (tier-2) first."
            )
        t2w = copy.deepcopy(t2)
        meta2 = await run_ground_truth_extraction_on_payload(
            t2w, topics_t2, mdl, is_tier2=True, config=cfg
        )
        k2 = f"{base}/{names.ground_truth_extraction_tier2_filtered}"
        u2 = upload_json_to_s3(k2, t2w)
        out["tier2"] = {"s3_key": k2, "s3_url": u2, "meta": meta2}
        man["ground_truth_extraction_tier2_s3_url"] = u2
        man["ground_truth_extraction_tier2_at"] = datetime.now(timezone.utc).isoformat()

    man_for_s3 = {k: v for k, v in man.items() if k != "manifest_s3_url"}
    upload_json_to_s3(mpath, man_for_s3)
    return out
