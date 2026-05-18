"""
LinkedIn room ``tiered_posts/`` S3 object names — loaded from :file:`artifacts.yaml`.

All services that read or write tier JSON, filtered JSON, or theme mapping JSON
should use :func:`get_linkedin_tiered_s3_artifact_names` instead of string literals.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

import yaml

_REQUIRED = (
    "tiered_folder",
    "tier_1_authored",
    "tier_2_reposts_and_comments",
    "tier_3_sparse",
    "manifest",
    "tier_1_authored_filtered",
    "tier_2_reposts_and_comments_filtered",
    "discovered_category_topic_mapping_tier1_filtered",
    "discovered_category_topic_mapping_tier2_filtered",
    "contextual_stimulus_extraction_tier1_filtered",
    "contextual_stimulus_extraction_tier2_filtered",
    "ground_truth_extraction_tier1_filtered",
    "ground_truth_extraction_tier2_filtered",
    "initial_prediction_tier1",
    "initial_prediction_tier2",
    "linkedin_sgo_predictions_tier1",
    "linkedin_sgo_predictions_tier2",
    "linkedin_sgo_evolution_state_tier1",
    "linkedin_sgo_evolution_state_tier2",
    "linkedin_sgo_memory_batches_tier1",
    "linkedin_sgo_memory_batches_tier2",
    "linkedin_sgo_aggregate_deltas_tier1",
    "linkedin_sgo_aggregate_deltas_tier2",
)


@dataclass(frozen=True)
class LinkedinTieredS3ArtifactNames:
    """Basenames and folder segment for tiered-post pipeline artifacts on S3."""

    tiered_folder: str
    tier_1_authored: str
    tier_2_reposts_and_comments: str
    tier_3_sparse: str
    manifest: str
    tier_1_authored_filtered: str
    tier_2_reposts_and_comments_filtered: str
    discovered_category_topic_mapping_tier1_filtered: str
    discovered_category_topic_mapping_tier2_filtered: str
    contextual_stimulus_extraction_tier1_filtered: str
    contextual_stimulus_extraction_tier2_filtered: str
    ground_truth_extraction_tier1_filtered: str
    ground_truth_extraction_tier2_filtered: str
    initial_prediction_tier1: str
    initial_prediction_tier2: str
    linkedin_sgo_predictions_tier1: str
    linkedin_sgo_predictions_tier2: str
    linkedin_sgo_evolution_state_tier1: str
    linkedin_sgo_evolution_state_tier2: str
    linkedin_sgo_memory_batches_tier1: str
    linkedin_sgo_memory_batches_tier2: str
    linkedin_sgo_aggregate_deltas_tier1: str
    linkedin_sgo_aggregate_deltas_tier2: str


def _artifacts_yaml_path() -> Path:
    return Path(__file__).resolve().with_name("artifacts.yaml")


@lru_cache(maxsize=1)
def get_linkedin_tiered_s3_artifact_names() -> LinkedinTieredS3ArtifactNames:
    path = _artifacts_yaml_path()
    raw: Mapping[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    missing = [k for k in _REQUIRED if not raw.get(k) or not str(raw.get(k)).strip()]
    if missing:
        raise KeyError(
            f"{path} must define non-empty string keys: {', '.join(_REQUIRED)}. Missing/empty: {missing}"
        )
    return LinkedinTieredS3ArtifactNames(
        tiered_folder=str(raw["tiered_folder"]).strip(),
        tier_1_authored=str(raw["tier_1_authored"]).strip(),
        tier_2_reposts_and_comments=str(raw["tier_2_reposts_and_comments"]).strip(),
        tier_3_sparse=str(raw["tier_3_sparse"]).strip(),
        manifest=str(raw["manifest"]).strip(),
        tier_1_authored_filtered=str(raw["tier_1_authored_filtered"]).strip(),
        tier_2_reposts_and_comments_filtered=str(raw["tier_2_reposts_and_comments_filtered"]).strip(),
        discovered_category_topic_mapping_tier1_filtered=str(
            raw["discovered_category_topic_mapping_tier1_filtered"]
        ).strip(),
        discovered_category_topic_mapping_tier2_filtered=str(
            raw["discovered_category_topic_mapping_tier2_filtered"]
        ).strip(),
        contextual_stimulus_extraction_tier1_filtered=str(
            raw["contextual_stimulus_extraction_tier1_filtered"]
        ).strip(),
        contextual_stimulus_extraction_tier2_filtered=str(
            raw["contextual_stimulus_extraction_tier2_filtered"]
        ).strip(),
        ground_truth_extraction_tier1_filtered=str(
            raw["ground_truth_extraction_tier1_filtered"]
        ).strip(),
        ground_truth_extraction_tier2_filtered=str(
            raw["ground_truth_extraction_tier2_filtered"]
        ).strip(),
        initial_prediction_tier1=str(raw["initial_prediction_tier1"]).strip(),
        initial_prediction_tier2=str(raw["initial_prediction_tier2"]).strip(),
        linkedin_sgo_predictions_tier1=str(raw["linkedin_sgo_predictions_tier1"]).strip(),
        linkedin_sgo_predictions_tier2=str(raw["linkedin_sgo_predictions_tier2"]).strip(),
        linkedin_sgo_evolution_state_tier1=str(raw["linkedin_sgo_evolution_state_tier1"]).strip(),
        linkedin_sgo_evolution_state_tier2=str(raw["linkedin_sgo_evolution_state_tier2"]).strip(),
        linkedin_sgo_memory_batches_tier1=str(raw["linkedin_sgo_memory_batches_tier1"]).strip(),
        linkedin_sgo_memory_batches_tier2=str(raw["linkedin_sgo_memory_batches_tier2"]).strip(),
        linkedin_sgo_aggregate_deltas_tier1=str(raw["linkedin_sgo_aggregate_deltas_tier1"]).strip(),
        linkedin_sgo_aggregate_deltas_tier2=str(raw["linkedin_sgo_aggregate_deltas_tier2"]).strip(),
    )


__all__ = (
    "LinkedinTieredS3ArtifactNames",
    "get_linkedin_tiered_s3_artifact_names",
)
