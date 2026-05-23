"""
Load ``linkedin_sgo_pipeline.yaml`` for LinkedIn delta-method CLI model defaults.

Used by ``linkedin/tier1_delta_method_predictions.py`` so Part B memory, refine, and shrink
models are not hardcoded in argparse.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

_CONFIG_DIR = Path(__file__).resolve().parent
_DEFAULT_YAML = _CONFIG_DIR / "linkedin_sgo_pipeline.yaml"

_CACHE: Optional[Dict[str, Any]] = None
_CACHE_PATH: Optional[Path] = None


def _config_path() -> Path:
    env = os.environ.get("LINKEDIN_SGO_PIPELINE_CONFIG", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return _DEFAULT_YAML


def _resolve_model(
    yaml_key: str,
    models: Dict[str, Any],
    flat: Dict[str, Any],
    env_var: str,
    *,
    fallback_key: Optional[str] = None,
    default: str = "o3",
) -> str:
    env_val = os.environ.get(env_var, "").strip()
    if env_val:
        return env_val
    flat_map = {
        "part_a": "gen_model",
        "topic": "topic_model",
        "i0": "i0_model",
        "memory_batch": "linkedin_root_cause_model",
        "refine": "refine_model",
        "shrink": "shrink_model",
        "qual_schema": "qual_schema_model",
    }
    flat_attr = flat_map.get(yaml_key)
    if flat_attr:
        v = flat.get(flat_attr)
        if v is not None and str(v).strip():
            return str(v).strip()
    v = models.get(yaml_key)
    if v is not None and str(v).strip():
        return str(v).strip()
    if fallback_key:
        fb = models.get(fallback_key) or flat.get(flat_map.get(fallback_key, ""))
        if fb is not None and str(fb).strip():
            return str(fb).strip()
    return default


def load_linkedin_sgo_pipeline_config(*, force_reload: bool = False) -> Dict[str, Any]:
    """Return raw YAML mapping."""
    global _CACHE, _CACHE_PATH
    p = _config_path()
    if not force_reload and _CACHE is not None and _CACHE_PATH == p:
        return _CACHE
    if not p.is_file():
        raise FileNotFoundError(f"LinkedIn SGO pipeline config not found: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"Invalid LinkedIn SGO pipeline config (expected mapping): {p}")
    _CACHE = raw
    _CACHE_PATH = p
    return raw


def linkedin_sgo_model_defaults(*, force_reload: bool = False) -> Dict[str, str]:
    """
    Model names for argparse defaults and run_all fallbacks.

    Keys: ``gen_model``, ``topic_model``, ``i0_model``, ``linkedin_root_cause_model``,
    ``refine_model``, ``shrink_model``, ``qual_schema_model``.
    """
    raw = load_linkedin_sgo_pipeline_config(force_reload=force_reload)
    models = raw.get("models") if isinstance(raw.get("models"), dict) else {}
    if not isinstance(models, dict):
        models = {}
    part_a = _resolve_model("part_a", models, raw, "LINKEDIN_PART_A_MODEL")
    return {
        "gen_model": part_a,
        "topic_model": _resolve_model("topic", models, raw, "LINKEDIN_TOPIC_MODEL"),
        "i0_model": _resolve_model(
            "i0",
            models,
            raw,
            "LINKEDIN_I0_MODEL",
            fallback_key="part_a",
            default=part_a,
        ),
        "linkedin_root_cause_model": _resolve_model(
            "memory_batch",
            models,
            raw,
            "LINKEDIN_MEMORY_BATCH_MODEL",
        ),
        "refine_model": _resolve_model(
            "refine",
            models,
            raw,
            "LINKEDIN_REFINE_MODEL",
        ),
        "shrink_model": _resolve_model(
            "shrink",
            models,
            raw,
            "LINKEDIN_SHRINK_MODEL",
        ),
        "qual_schema_model": _resolve_model(
            "qual_schema",
            models,
            raw,
            "LINKEDIN_QUAL_SCHEMA_MODEL",
            fallback_key="refine",
        ),
    }


__all__ = [
    "load_linkedin_sgo_pipeline_config",
    "linkedin_sgo_model_defaults",
]
