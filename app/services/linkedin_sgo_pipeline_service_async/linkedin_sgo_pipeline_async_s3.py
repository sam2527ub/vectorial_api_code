"""S3 download/upload for LinkedIn SGO delta-method runs (tier 1 / tier 2)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from app.linkedin_tiered_s3 import LinkedinTieredS3ArtifactNames, get_linkedin_tiered_s3_artifact_names
from app.utils.s3_utils import get_s3_key_for_audience, try_fetch_json_from_s3

from .linkedin_sgo_pipeline_async_s3_workersafe import upload_json_to_s3_worker, upload_text_to_s3_worker

from app.services.linkedin_initial_prediction_service.initial_prediction_s3 import (
    download_room_profiles_from_s3,
    profile_ids_from_contextual_payload,
)

logger = logging.getLogger(__name__)


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def download_sgo_inputs_to_workdir(
    *,
    audience_room_id: str,
    enterprise_name: Optional[str],
    source: Optional[str],
    tier: int,
    work_dir: Path,
    profiles_root: Path,
    art: Optional[LinkedinTieredS3ArtifactNames] = None,
) -> Tuple[Path, Path, Path, Path, Path, int]:
    """
    Fetch tier-specific stimulus (ground-truth contextual JSON), mapping, description, i0 predictions,
    and profile JSONs into ``profiles_root``.

    Returns ``(contextual_json_path, mapping_json_path, description_json_path, precomputed_i0_path, profiles_root, num_profiles)``.
    ``num_profiles`` is len(profile ids in stimulus), i.e. posts/users processed each outer iteration.
    """
    a = art or get_linkedin_tiered_s3_artifact_names()
    base = get_s3_key_for_audience(
        audience_room_id, a.tiered_folder, enterprise_name, source
    ).rstrip("/")

    if tier == 2:
        stim_name = a.ground_truth_extraction_tier2_filtered
        map_name = a.discovered_category_topic_mapping_tier2_filtered
        i0_name = a.initial_prediction_tier2
    else:
        stim_name = a.ground_truth_extraction_tier1_filtered
        map_name = a.discovered_category_topic_mapping_tier1_filtered
        i0_name = a.initial_prediction_tier1

    stim = try_fetch_json_from_s3(f"{base}/{stim_name}")
    if not stim or not isinstance(stim, dict):
        raise FileNotFoundError(f"S3 missing or invalid {base}/{stim_name}")
    mapping = try_fetch_json_from_s3(f"{base}/{map_name}")
    if not mapping or not isinstance(mapping, dict):
        raise FileNotFoundError(f"S3 missing or invalid {base}/{map_name}")

    desc_key = get_s3_key_for_audience(audience_room_id, "description.json", enterprise_name, source)
    desc = try_fetch_json_from_s3(desc_key)
    if not desc or not isinstance(desc, dict):
        raise FileNotFoundError(f"S3 missing description.json: {desc_key}")

    i0 = try_fetch_json_from_s3(f"{base}/{i0_name}")
    if not i0 or not isinstance(i0, dict):
        raise FileNotFoundError(f"S3 missing or invalid {base}/{i0_name}")

    work_dir.mkdir(parents=True, exist_ok=True)
    contextual_path = work_dir / "sgo_contextual_stimulus.json"
    mapping_path = work_dir / "sgo_category_topic_mapping.json"
    description_path = work_dir / "sgo_description.json"
    i0_path = work_dir / "sgo_precomputed_i0.json"

    _write_json(contextual_path, stim)
    _write_json(mapping_path, mapping)
    _write_json(description_path, desc)
    _write_json(i0_path, i0)

    pids = profile_ids_from_contextual_payload(stim)
    num_profiles = len(pids)
    n = download_room_profiles_from_s3(
        audience_room_id=audience_room_id,
        profile_ids=pids,
        profiles_root=profiles_root,
        enterprise_name=enterprise_name,
        source=source,
    )
    logger.info("[SGO_S3] downloaded %s inputs → %s (%s profiles)", tier, work_dir, n)

    return contextual_path, mapping_path, description_path, i0_path, profiles_root, num_profiles


def prepare_tier2_evolution_baseline_dir_from_s3(
    *,
    audience_room_id: str,
    enterprise_name: Optional[str],
    source: Optional[str],
    tier1_evolution_parent: Path,
    art: Optional[LinkedinTieredS3ArtifactNames] = None,
) -> Path:
    """
    Download tier-1 evolution JSON from S3 into ``tier1_evolution_parent/evolution/evolution_state.json``.

    Returns ``tier1_evolution_parent`` for use as ``tier1_evolution_work_dir``.
    """
    a = art or get_linkedin_tiered_s3_artifact_names()
    base = get_s3_key_for_audience(
        audience_room_id, a.tiered_folder, enterprise_name, source
    ).rstrip("/")
    key = f"{base}/{a.linkedin_sgo_evolution_state_tier1}"
    doc = try_fetch_json_from_s3(key)
    if not doc or not isinstance(doc, dict):
        raise FileNotFoundError(
            f"Tier-2 SGO requires tier-1 evolution on S3 at {key} (complete tier-1 SGO first)."
        )
    evo_dir = tier1_evolution_parent / "evolution"
    evo_dir.mkdir(parents=True, exist_ok=True)
    dest = evo_dir / "evolution_state.json"
    _write_json(dest, doc)
    logger.info("[SGO_S3] tier-2 baseline evolution from %s → %s", key, dest)
    return tier1_evolution_parent


def upload_sgo_outputs_from_workdir(
    *,
    audience_room_id: str,
    enterprise_name: Optional[str],
    source: Optional[str],
    tier: int,
    work_dir: Path,
    art: Optional[LinkedinTieredS3ArtifactNames] = None,
) -> Dict[str, str]:
    """Upload predictions, evolution state, memory batches, and aggregate deltas. Returns url by logical key."""
    a = art or get_linkedin_tiered_s3_artifact_names()
    base = get_s3_key_for_audience(
        audience_room_id, a.tiered_folder, enterprise_name, source
    ).rstrip("/")

    if tier == 2:
        pred_key = a.linkedin_sgo_predictions_tier2
        evo_key = a.linkedin_sgo_evolution_state_tier2
        mem_key = a.linkedin_sgo_memory_batches_tier2
        agg_key = a.linkedin_sgo_aggregate_deltas_tier2
    else:
        pred_key = a.linkedin_sgo_predictions_tier1
        evo_key = a.linkedin_sgo_evolution_state_tier1
        mem_key = a.linkedin_sgo_memory_batches_tier1
        agg_key = a.linkedin_sgo_aggregate_deltas_tier1

    out_urls: Dict[str, str] = {}

    pred_path = work_dir / "delta_method_predictions.json"
    if pred_path.is_file():
        doc = json.loads(pred_path.read_text(encoding="utf-8"))
        out_urls["predictions"] = upload_json_to_s3_worker(f"{base}/{pred_key}", doc)

    evo_path = work_dir / "evolution" / "evolution_state.json"
    if evo_path.is_file():
        doc = json.loads(evo_path.read_text(encoding="utf-8"))
        out_urls["evolution_state"] = upload_json_to_s3_worker(f"{base}/{evo_key}", doc)

    mem_path = work_dir / "memory" / "batch_analyses.json"
    if mem_path.is_file():
        doc = json.loads(mem_path.read_text(encoding="utf-8"))
        out_urls["memory_batches"] = upload_json_to_s3_worker(f"{base}/{mem_key}", doc)

    agg_path = work_dir / "linkedin_tier1_all_reviews_deltas.json"
    if agg_path.is_file():
        doc = json.loads(agg_path.read_text(encoding="utf-8"))
        out_urls["aggregate_deltas"] = upload_json_to_s3_worker(f"{base}/{agg_key}", doc)

    return out_urls


def update_manifest_linkedin_sgo(
    base: str,
    man_key: str,
    tier: int,
    urls: Dict[str, str],
) -> None:
    man: Dict[str, Any] = try_fetch_json_from_s3(f"{base}/{man_key}") or {}
    if not isinstance(man, dict):
        man = {}
    now = datetime.now(timezone.utc).isoformat()
    prefix = "linkedin_sgo_tier2" if tier == 2 else "linkedin_sgo_tier1"
    for logical, url in urls.items():
        man[f"{prefix}_{logical}_s3_url"] = url
    man[f"{prefix}_at"] = now
    upload_json_to_s3_worker(
        f"{base}/{man_key}", {k: v for k, v in man.items() if k != "manifest_s3_url"}
    )
