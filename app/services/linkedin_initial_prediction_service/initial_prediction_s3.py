"""S3 fetch/upload helpers for initial prediction (tiered_posts + room description + profiles)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.linkedin_tiered_s3 import get_linkedin_tiered_s3_artifact_names
from app.utils.s3_utils import get_s3_key_for_audience, try_fetch_json_from_s3, upload_json_to_s3

logger = logging.getLogger(__name__)


def partial_s3_key_for_artifact(base: str, artifact_relative_path: str) -> str:
    return f"{base.rstrip('/')}/{artifact_relative_path}.partial"


def fetch_s3_inputs_for_initial_prediction(
    *,
    audience_room_id: str,
    tier: int,
    enterprise_name: Optional[str],
    source: Optional[str],
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """Download ground-truth stimulus, theme mapping, and ``description.json`` from S3."""
    art = get_linkedin_tiered_s3_artifact_names()
    base = get_s3_key_for_audience(
        audience_room_id, art.tiered_folder, enterprise_name, source
    ).rstrip("/")

    if tier == 2:
        stim_name = art.ground_truth_extraction_tier2_filtered
        map_name = art.discovered_category_topic_mapping_tier2_filtered
    else:
        stim_name = art.ground_truth_extraction_tier1_filtered
        map_name = art.discovered_category_topic_mapping_tier1_filtered

    stim = try_fetch_json_from_s3(f"{base}/{stim_name}")
    if not stim:
        raise FileNotFoundError(
            f"S3 missing {base}/{stim_name} — run ground-truth extraction for tier {tier} first."
        )
    mapping = try_fetch_json_from_s3(f"{base}/{map_name}")
    if not mapping:
        raise FileNotFoundError(f"S3 missing {base}/{map_name}")
    desc_key = get_s3_key_for_audience(audience_room_id, "description.json", enterprise_name, source)
    desc = try_fetch_json_from_s3(desc_key)
    if not desc:
        raise FileNotFoundError(f"S3 missing {desc_key}")
    return stim, mapping, desc


def download_room_profiles_from_s3(
    *,
    audience_room_id: str,
    profile_ids: Iterable[str],
    profiles_root: Path,
    enterprise_name: Optional[str],
    source: Optional[str],
) -> int:
    """Fetch ``profiles/<id>/profile.json`` into ``profiles_root/<id>/profile.json``."""
    profiles_root.mkdir(parents=True, exist_ok=True)
    n = 0
    for raw in profile_ids:
        uid = str(raw).strip()
        if not uid:
            continue
        key = get_s3_key_for_audience(
            audience_room_id, f"profiles/{uid}/profile.json", enterprise_name, source
        )
        doc = try_fetch_json_from_s3(key)
        if not doc or not isinstance(doc, dict):
            logger.warning("[initial_prediction profiles] missing or invalid S3 object: %s", key)
            continue
        pdir = profiles_root / uid
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "profile.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        n += 1
    logger.info("[initial_prediction] downloaded %s profile.json file(s) → %s", n, profiles_root)
    return n


def profile_ids_from_contextual_payload(payload: Dict[str, Any]) -> List[str]:
    """User ids under ``results_by_user`` (matches ``profiles/<id>/`` on S3)."""
    rb = payload.get("results_by_user")
    if not isinstance(rb, dict):
        users = payload.get("users")
        if isinstance(users, dict):
            rb = users
        else:
            return []
    return [str(k).strip() for k in rb.keys() if str(k).strip()]


def update_manifest_initial_prediction(base: str, man_key: str, tier: int, s3_url: str) -> None:
    man: Dict[str, Any] = try_fetch_json_from_s3(f"{base}/{man_key}") or {}
    if not isinstance(man, dict):
        man = {}
    now = datetime.now(timezone.utc).isoformat()
    if tier == 2:
        man["initial_prediction_tier2_s3_url"] = s3_url
        man["initial_prediction_tier2_at"] = now
    else:
        man["initial_prediction_tier1_s3_url"] = s3_url
        man["initial_prediction_tier1_at"] = now
    upload_json_to_s3(
        f"{base}/{man_key}", {k: v for k, v in man.items() if k != "manifest_s3_url"}
    )
