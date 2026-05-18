"""
LinkedIn **ground-truth** per-topic labels (LLM Yes/No + logprobs) for contextual stimulus JSON.

* **S3 (one-shot or bounded chunk job)** — :func:`ground_truth_extraction_room_runner.run_ground_truth_extraction_for_s3_artifacts`
* **In-memory** — :func:`ground_truth_extraction_processor.run_ground_truth_extraction_on_payload`
* **Config** — ``ground_truth_extraction_config.yaml`` (``GROUND_TRUTH_EXTRACTION_CONFIG``)
"""

from .errors import GroundTruthExtractionError
from .ground_truth_extraction_processor import (
    enrich_contextual_stimulus_with_topic_presence,
    run_ground_truth_extraction_on_payload,
    topics_mapping_from_mapping_object,
)
from .ground_truth_extraction_room_runner import run_ground_truth_extraction_for_s3_artifacts

# Back-compat aliases
PostTopicPresenceError = GroundTruthExtractionError
run_post_topic_presence_for_s3_artifacts = run_ground_truth_extraction_for_s3_artifacts

__all__ = (
    "GroundTruthExtractionError",
    "run_ground_truth_extraction_on_payload",
    "run_ground_truth_extraction_for_s3_artifacts",
    "enrich_contextual_stimulus_with_topic_presence",
    "topics_mapping_from_mapping_object",
    "run_post_topic_presence_for_s3_artifacts",
    "PostTopicPresenceError",
)
