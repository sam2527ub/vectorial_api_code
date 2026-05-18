"""LinkedIn SGO delta-method pipeline — production async package (``vendored_sgo/``, S3 staging).

Exports are lazy (PEP 562): importing ``app.services.linkedin_sgo_pipeline_service_async`` must not
pull DB/S3-heavy submodules until an attribute is accessed.
"""

from __future__ import annotations

import importlib
from typing import Any

__all__ = (
    "LinkedInSGOPipelineConfigBundle",
    "LinkedInSGOPipelineError",
    "VENDORED_SGO_ROOT",
    "build_namespace_for_chunk",
    "ensure_required_paths",
    "get_default_namespace",
    "load_linkedin_sgo_pipeline_async_config",
    "load_linkedin_sgo_pipeline_config",
    "run_linkedin_sgo_delta_pipeline",
    "run_linkedin_sgo_delta_pipeline_sync",
    "run_linkedin_sgo_from_yaml_overrides",
    "run_linkedin_sgo_pipeline_async",
    "run_linkedin_sgo_pipeline_async_process_chunk",
    "run_linkedin_sgo_pipeline_async_sync",
    "run_linkedin_sgo_pipeline_job_chunk",
    "run_sgo_pipeline_job_chunk",
    "vendored_sgo_sys_path",
)

_ATTR_TO_MODULE = {
    "LinkedInSGOPipelineConfigBundle": "linkedin_sgo_pipeline_async_config",
    "ensure_required_paths": "linkedin_sgo_pipeline_async_config",
    "get_default_namespace": "linkedin_sgo_pipeline_async_config",
    "load_linkedin_sgo_pipeline_async_config": "linkedin_sgo_pipeline_async_config",
    "load_linkedin_sgo_pipeline_config": "linkedin_sgo_pipeline_async_config",
    "LinkedInSGOPipelineError": "linkedin_sgo_pipeline_async_errors",
    "VENDORED_SGO_ROOT": "linkedin_sgo_pipeline_async_runner",
    "run_linkedin_sgo_delta_pipeline": "linkedin_sgo_pipeline_async_runner",
    "run_linkedin_sgo_delta_pipeline_sync": "linkedin_sgo_pipeline_async_runner",
    "run_linkedin_sgo_from_yaml_overrides": "linkedin_sgo_pipeline_async_runner",
    "run_linkedin_sgo_pipeline_async": "linkedin_sgo_pipeline_async_runner",
    "run_linkedin_sgo_pipeline_async_sync": "linkedin_sgo_pipeline_async_runner",
    "vendored_sgo_sys_path": "linkedin_sgo_pipeline_async_runner",
    "build_namespace_for_chunk": "linkedin_sgo_pipeline_async_chunk_job",
    "run_linkedin_sgo_pipeline_async_process_chunk": "linkedin_sgo_pipeline_async_chunk_job",
    "run_sgo_pipeline_job_chunk": "linkedin_sgo_pipeline_async_chunk_job",
    "run_linkedin_sgo_pipeline_job_chunk": "linkedin_sgo_pipeline_async_worker",
}


def __getattr__(name: str) -> Any:
    mod_name = _ATTR_TO_MODULE.get(name)
    if mod_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    mod = importlib.import_module(f".{mod_name}", __package__)
    return getattr(mod, name)
