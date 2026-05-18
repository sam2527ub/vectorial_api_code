"""
Production entry: read **filtered** tiered_posts JSON from S3, run theme discovery, upload results.

Input and output object basenames come from :file:`app/linkedin_tiered_s3/artifacts.yaml` (not hardcoded here).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, Literal, Optional

from app import database
from app.config import s3_bucket, s3_client
from app.linkedin_tiered_s3 import get_linkedin_tiered_s3_artifact_names
from app.utils.s3_utils import (
    get_audience_type_from_source,
    get_s3_key_for_audience,
    try_fetch_json_from_s3,
    upload_json_to_s3,
)
from .pipeline import ThemeDiscoveryPipelineError, run
from .tier_production_config import merge_cli_with_tier_profile

log = logging.getLogger(__name__)


def _tier_base(
    audience_room_id: str,
    enterprise_name: Optional[str],
    source: Optional[str],
) -> str:
    a = get_linkedin_tiered_s3_artifact_names()
    return get_s3_key_for_audience(
        audience_room_id, a.tiered_folder, enterprise_name, source
    ).rstrip("/")


def _load_json_basename(
    base: str, basename: str
) -> Optional[Dict[str, Any]]:
    raw = try_fetch_json_from_s3(f"{base}/{basename}")
    if raw and isinstance(raw, dict) and len(raw) > 0:
        return raw
    return None


def _ns_for_production(
    tier: str,
    tier1_path: str,
    out_path: str,
    model: str,
    tier2_aux_path: Optional[str],
) -> argparse.Namespace:
    p = argparse.Namespace(
        tier=tier,
        tier1=tier1_path,
        tier2=tier2_aux_path,
        output=out_path,
        category="",
        model=model,
        context_tokens=0,
        max_prompt_tokens=0,
        safety_margin=None,
        map_max_output=None,
        map_post_partitions=None,
        max_rounds=None,
        sample_size_per_round=25,
        initial_themes_count=10,
        subsequent_rounds_max_themes=None,
        min_categories=None,
        max_categories=None,
        seed=42,
        test_rounds=0,
    )
    merge_cli_with_tier_profile(p)
    return p


async def run_linkedin_theme_discovery_for_room(
    audience_room_id: str,
    enterprise_name: Optional[str] = None,
    tier: Literal["1", "2"] = "1",
    model: Optional[str] = None,
    include_tier2_aux: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Return manifest-like dict with ``mapping_s3_url``, or None if room/S3/tier data missing;
    same skip shape as other room runners for non-LinkedIn rooms.
    """
    if not database.is_audience_db_available():
        log.warning("run_linkedin_theme_discovery_for_room: audience DB unavailable")
        return None
    if not s3_client or not s3_bucket:
        log.warning("run_linkedin_theme_discovery_for_room: S3 not configured")
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
    mdl = model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    tmpdir = tempfile.mkdtemp(prefix="ld_theme_")
    try:
        t1p = os.path.join(tmpdir, "tier_input.json")
        out_path = os.path.join(tmpdir, "out.json")
        tier2_aux_path: Optional[str] = None

        if tier == "2":
            t2 = _load_json_basename(base, art.tier_2_reposts_and_comments_filtered)
            if not t2:
                raise ThemeDiscoveryPipelineError(
                    f"No {art.tier_2_reposts_and_comments_filtered!r} on S3. Run filter-tiered-posts so filtered tier-2 data exists."
                )
            # Write as S3: filter output uses top-level ``users``; ``load_tier*`` in pipeline supports that
            # (and ``results_by_user``). Do not wrap the whole file in ``{results_by_user: ...}`` or posts are lost.
            with open(t1p, "w", encoding="utf-8") as f:
                json.dump(t2, f, ensure_ascii=False, indent=2)
            out_name = art.discovered_category_topic_mapping_tier2_filtered
        else:
            t1 = _load_json_basename(base, art.tier_1_authored_filtered)
            if not t1:
                raise ThemeDiscoveryPipelineError(
                    f"No {art.tier_1_authored_filtered!r} on S3. Run filter-tiered-posts so filtered tier-1 data exists."
                )
            with open(t1p, "w", encoding="utf-8") as f:
                json.dump(t1, f, ensure_ascii=False, indent=2)
            if include_tier2_aux:
                t2a = _load_json_basename(base, art.tier_2_reposts_and_comments_filtered)
                if t2a:
                    tier2_aux_path = os.path.join(tmpdir, "tier2_aux.json")
                    with open(tier2_aux_path, "w", encoding="utf-8") as f:
                        json.dump(t2a, f, ensure_ascii=False, indent=2)
            out_name = art.discovered_category_topic_mapping_tier1_filtered

        args = _ns_for_production(
            tier, t1p, out_path, mdl, tier2_aux_path if tier == "1" else None
        )
        await run(args)
        with open(out_path, encoding="utf-8") as f:
            payload: Dict[str, Any] = json.load(f)
        key = f"{base}/{out_name}"
        url = upload_json_to_s3(key, payload)
        mpath = f"{base}/{art.manifest}"
        man = try_fetch_json_from_s3(mpath) or {}
        if isinstance(man, dict):
            if tier == "2":
                man["theme_discovery_tier2_mapping_s3_url"] = url
            else:
                man["theme_discovery_tier1_mapping_s3_url"] = url
            man["theme_discovery_last_run_at"] = datetime.now(timezone.utc).isoformat()
            man["theme_discovery_tier"] = tier
            upload_json_to_s3(mpath, man)
        return {
            "audience_room_id": audience_room_id,
            "tier": tier,
            "mapping_s3_key": key,
            "mapping_s3_url": url,
            "model": mdl,
            "include_tier2_aux": include_tier2_aux if tier == "1" else None,
        }
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _result_without_room_id(d: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in d.items() if k != "audience_room_id"}


async def run_linkedin_theme_discovery_both_tiers_for_room(
    audience_room_id: str,
    enterprise_name: Optional[str] = None,
    model: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Run tier-1 then tier-2 theme discovery. Basenames: ``app/linkedin_tiered_s3/artifacts.yaml``.

    The tier-1 run uses authored text only, then tier-2 uses reply/interaction text, so the two
    discovered mapping files are independent.
    Stops after tier 1 if the room is skipped or not LinkedIn. If tier 1 succeeds but
    tier 2 has no S3 data, the second run raises ``ThemeDiscoveryPipelineError``; tier-1
    output remains in S3 from the first run.
    """
    r1 = await run_linkedin_theme_discovery_for_room(
        audience_room_id=audience_room_id,
        enterprise_name=enterprise_name,
        tier="1",
        model=model,
        include_tier2_aux=False,
    )
    if r1 is None or r1.get("skipped"):
        return r1

    r2 = await run_linkedin_theme_discovery_for_room(
        audience_room_id=audience_room_id,
        enterprise_name=enterprise_name,
        tier="2",
        model=model,
        include_tier2_aux=False,
    )
    if r2 is None:
        return None
    if r2.get("skipped"):
        return {
            "audience_room_id": audience_room_id,
            "mode": "both",
            "partial": True,
            "reason": "tier2_skipped_after_tier1",
            "tier1": _result_without_room_id(r1),
            "tier2": _result_without_room_id(r2),
        }
    return {
        "audience_room_id": audience_room_id,
        "mode": "both",
        "tier1": _result_without_room_id(r1),
        "tier2": _result_without_room_id(r2),
    }


__all__ = (
    "run_linkedin_theme_discovery_both_tiers_for_room",
    "run_linkedin_theme_discovery_for_room",
)
