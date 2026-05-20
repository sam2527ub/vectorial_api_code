"""
Backward-compatible imports — implementation lives under ``app.services.linkedin_initial_prediction_service``.

Scripts should not duplicate production logic; use the service package or HTTP API.
"""

from __future__ import annotations

from app.services.linkedin_initial_prediction_service.initial_prediction_processor import (
    run_initial_prediction,
)
from app.services.linkedin_initial_prediction_service.initial_prediction_s3 import (
    download_room_profiles_from_s3,
    fetch_s3_inputs_for_initial_prediction,
    profile_ids_from_contextual_payload,
)
from app.services.linkedin_initial_prediction_service.initial_prediction_types import (
    InitialPredictionRunParams,
    InitialPredictionRunResult,
    PIPELINE_ID,
    build_initial_prediction_artifact,
    resolve_openai_api_key_for_tier,
    slim_initial_prediction_record,
    write_prediction_json_local,
)

__all__ = (
    "PIPELINE_ID",
    "InitialPredictionRunParams",
    "InitialPredictionRunResult",
    "build_initial_prediction_artifact",
    "download_room_profiles_from_s3",
    "fetch_s3_inputs_for_initial_prediction",
    "profile_ids_from_contextual_payload",
    "resolve_openai_api_key_for_tier",
    "run_initial_prediction",
    "slim_initial_prediction_record",
    "write_prediction_json_local",
)


def ensure_repo_root_importable() -> None:
    """Deprecated no-op; the app package does not require mutating ``sys.path`` for production."""
