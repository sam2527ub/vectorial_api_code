"""
Async orchestration: download tier-specific inputs from S3, run SGO, upload tier-specific outputs.

Mirrors the pattern in ``linkedin_ground_truth_extraction_service`` / ``linkedin_initial_prediction_service``:
no imports from ``scripts/`` — only ``vendored_sgo`` via :func:`linkedin_sgo_pipeline_async_runner.run_linkedin_sgo_pipeline_async`.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from app.config import logger, s3_bucket, s3_client
from app.linkedin_tiered_s3 import get_linkedin_tiered_s3_artifact_names
from app.utils.s3_utils import get_audience_type_from_source, get_s3_key_for_audience

from .linkedin_sgo_pipeline_async_config import ensure_required_paths, get_default_namespace
from .linkedin_sgo_pipeline_async_errors import LinkedInSGOPipelineError
from .linkedin_sgo_pipeline_async_runner import run_linkedin_sgo_pipeline_async
from .linkedin_sgo_pipeline_async_s3 import (
    download_sgo_inputs_to_workdir,
    prepare_tier2_evolution_baseline_dir_from_s3,
    update_manifest_linkedin_sgo,
    upload_sgo_outputs_from_workdir,
)


async def run_linkedin_sgo_pipeline_async_process_chunk(
    *,
    audience_room_id: str,
    enterprise_name: Optional[str] = None,
    source: Optional[str] = None,
    tier_mode: str = "tier1",
    overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    One-shot run: S3 inputs → temp ``work_dir`` → pipeline → upload artifacts + manifest.

    **Tier 1** reads ``ground_truth_extraction_tier{N}_filtered``, mapping, ``description.json``,
    ``initial_prediction_tier{N}``, and profiles.

    **Tier 2** additionally requires ``linkedin_sgo_evolution_state_tier1`` on S3 (complete tier 1 SGO first).
    """
    if not s3_client or not s3_bucket:
        raise LinkedInSGOPipelineError("S3 not configured")

    from app import database

    tm = str(tier_mode or "tier1").strip().lower()
    if tm not in ("tier1", "tier2"):
        tm = "tier1"
    tier_int = 2 if tm == "tier2" else 1

    room = database.find_audience_room_by_id(
        audience_room_id, include_profiles=False, enterprise_name=enterprise_name
    )
    if not room:
        raise LinkedInSGOPipelineError(f"Audience room not found: {audience_room_id}")
    if get_audience_type_from_source(room.source) != "linkedin-audience":
        return {"skipped": True, "reason": "not_linkedin_audience"}

    src = source if source is not None else room.source
    art = get_linkedin_tiered_s3_artifact_names()
    base = get_s3_key_for_audience(
        audience_room_id, art.tiered_folder, enterprise_name, src
    ).rstrip("/")

    with tempfile.TemporaryDirectory(prefix="linkedin_sgo_async_") as td:
        root = Path(td)
        work_dir = root / "work"
        profiles_root = root / "profiles"

        contextual_path, mapping_path, description_path, i0_path, profiles_root, _num_profiles = (
            download_sgo_inputs_to_workdir(
                audience_room_id=audience_room_id,
                enterprise_name=enterprise_name,
                source=src,
                tier=tier_int,
                work_dir=work_dir,
                profiles_root=profiles_root,
                art=art,
            )
        )

        tier1_evo_dir = ""
        if tier_int == 2:
            t1mirror = root / "tier1_evolution_baseline"
            prepare_tier2_evolution_baseline_dir_from_s3(
                audience_room_id=audience_room_id,
                enterprise_name=enterprise_name,
                source=src,
                tier1_evolution_parent=t1mirror,
                art=art,
            )
            tier1_evo_dir = str(t1mirror)

        merged: Dict[str, Any] = {
            "tier_mode": tm,
            "contextual_json": str(contextual_path),
            "description_json": str(description_path),
            "mapping_json": str(mapping_path),
            "profiles_root": str(profiles_root),
            "precomputed_i0_json": str(i0_path),
            "work_dir": str(work_dir),
            "output": str(work_dir / "delta_method_predictions.json"),
            "tier1_evolution_work_dir": tier1_evo_dir,
        }
        if overrides:
            merged.update({k: v for k, v in overrides.items() if v is not None})

        ns = get_default_namespace(merged)
        ensure_required_paths(ns)

        await run_linkedin_sgo_pipeline_async(ns)

        urls = upload_sgo_outputs_from_workdir(
            audience_room_id=audience_room_id,
            enterprise_name=enterprise_name,
            source=src,
            tier=tier_int,
            work_dir=work_dir,
            art=art,
        )
        update_manifest_linkedin_sgo(base, art.manifest, tier_int, urls)

        logger.info("[linkedin_sgo_pipeline_async] completed tier=%s urls=%s", tm, list(urls.keys()))
        return {
            "status": "ok",
            "audience_room_id": audience_room_id,
            "tier_mode": tm,
            "s3_urls": urls,
            "s3_base": base,
        }


def run_sgo_pipeline_job_chunk(
    *,
    overrides: Dict[str, Any],
    validate_paths: bool = True,
) -> None:
    """
    Run using explicit local paths (no S3 staging). Merges ``overrides`` onto YAML defaults.

    Prefer :func:`run_linkedin_sgo_pipeline_async_process_chunk` for production I/O.
    """
    ns = get_default_namespace(overrides)
    if validate_paths:
        ensure_required_paths(ns)
    logger.info(
        "[SGO_CHUNK] tier_mode=%s work_dir=%s",
        getattr(ns, "tier_mode", ""),
        getattr(ns, "work_dir", ""),
    )
    asyncio.run(run_linkedin_sgo_pipeline_async(ns))


def build_namespace_for_chunk(
    overrides: Optional[Dict[str, Any]] = None,
) -> Any:
    return get_default_namespace(overrides)
