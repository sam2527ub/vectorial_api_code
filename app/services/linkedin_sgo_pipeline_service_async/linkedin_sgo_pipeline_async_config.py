"""Load ``linkedin_sgo_pipeline_async_config.yaml`` and build ``argparse.Namespace`` for the vendored runner."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Dict, Optional

import yaml

from .linkedin_sgo_pipeline_async_errors import LinkedInSGOPipelineError

_LOCK = RLock()
_CFG_CACHE: Optional["LinkedInSGOPipelineConfigBundle"] = None
_CFG_CACHE_PATH: Optional[Path] = None


@dataclass(frozen=True)
class LinkedInSGOPipelineConfigBundle:
    """Frozen snapshot of YAML defaults plus resolved config path."""

    raw: Dict[str, Any]
    config_path: Path

    def build_namespace(self, overrides: Optional[Dict[str, Any]] = None) -> argparse.Namespace:
        merged: Dict[str, Any] = dict(self.raw)
        if overrides:
            merged.update({k: v for k, v in overrides.items() if v is not None})
        return _dict_to_namespace(merged)


def _config_path() -> Path:
    env = (
        os.environ.get("LINKEDIN_SGO_PIPELINE_ASYNC_CONFIG", "").strip()
        or os.environ.get("LINKEDIN_SGO_PIPELINE_CONFIG", "").strip()
    )
    if env:
        return Path(env).expanduser().resolve()
    try:
        from app.runtime_settings import pipeline_config_path

        rp = pipeline_config_path("linkedin_sgo_async")
        if rp is not None and rp.is_file():
            return rp
    except Exception:
        pass
    return Path(__file__).resolve().parent / "linkedin_sgo_pipeline_async_config.yaml"


def _dict_to_namespace(d: Dict[str, Any]) -> argparse.Namespace:
    ns = argparse.Namespace()
    merged = {**_NAMESPACE_OPTIONAL_DEFAULTS, **d}
    for k, v in merged.items():
        if k.startswith("_"):
            continue
        setattr(ns, str(k), v)
    return ns


# Keys the vendored CLI sets via argparse but async/Fargate builds from YAML only.
# Filling these here avoids AttributeError when YAML predates a new flag.
_NAMESPACE_OPTIONAL_DEFAULTS: Dict[str, Any] = {
    "group_summary_min_words": 500,
    "group_summary_max_words": 700,
    "max_traits_per_category": 15,
    "shrink_floor_words": 500,
    "shrink_hard_max_words": 700,
    "shrink_baseline_ratio": 1.22,
    "shrink_baseline_floor_ratio": 0.90,
    "refine_memory_batches": 12,
    "qual_shrink_every_n_refinements": 5,
    "no_shrink": False,
    "no_outlier_refine": False,
    "resume": False,
    "resume_user_pred_seed": "",
}


def load_linkedin_sgo_pipeline_async_config(
    *,
    force_reload: bool = False,
    path: Optional[Path] = None,
) -> LinkedInSGOPipelineConfigBundle:
    global _CFG_CACHE, _CFG_CACHE_PATH
    with _LOCK:
        p = path or _config_path()
        if (
            not force_reload
            and _CFG_CACHE is not None
            and _CFG_CACHE_PATH == p
            and p.is_file()
        ):
            return _CFG_CACHE
        if not p.is_file():
            raise LinkedInSGOPipelineError(f"LinkedIn SGO async pipeline config not found: {p}")
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise LinkedInSGOPipelineError(f"Invalid YAML (expected mapping): {p}")
        _CFG_CACHE = LinkedInSGOPipelineConfigBundle(raw=raw, config_path=p)
        _CFG_CACHE_PATH = p
        return _CFG_CACHE


def ensure_sgo_namespace_defaults(ns: argparse.Namespace) -> argparse.Namespace:
    for k, v in _NAMESPACE_OPTIONAL_DEFAULTS.items():
        if not hasattr(ns, k):
            setattr(ns, k, v)
    return ns


def get_default_namespace(overrides: Optional[Dict[str, Any]] = None) -> argparse.Namespace:
    ns = load_linkedin_sgo_pipeline_async_config().build_namespace(overrides)
    return ensure_sgo_namespace_defaults(ns)


def ensure_required_paths(
    ns: argparse.Namespace,
    *,
    strict_work_output: bool = False,
) -> None:
    missing = []
    for attr in (
        "contextual_json",
        "description_json",
        "mapping_json",
        "profiles_root",
        "precomputed_i0_json",
    ):
        v = getattr(ns, attr, None)
        if v is None or (isinstance(v, str) and not v.strip()):
            missing.append(attr)
    if strict_work_output:
        for attr in ("work_dir", "output"):
            v = getattr(ns, attr, None)
            if v is None or (isinstance(v, str) and not v.strip()):
                missing.append(attr)
    if missing:
        raise LinkedInSGOPipelineError(
            "Missing required path configuration: "
            + ", ".join(missing)
            + ". Set them in YAML overrides or job payload."
        )
    tm = str(getattr(ns, "tier_mode", "tier1") or "tier1").strip().lower()
    if tm == "tier2":
        if bool(getattr(ns, "sgo_skip_tier1_evolution_requirement", False)):
            pass
        else:
            tw = str(getattr(ns, "tier1_evolution_work_dir", "") or "").strip()
            if not tw:
                raise LinkedInSGOPipelineError(
                    "tier_mode=tier2 requires tier1_evolution_work_dir unless resuming from checkpoint "
                    "(set sgo_skip_tier1_evolution_requirement)."
                )


# Back-compat names
load_linkedin_sgo_pipeline_config = load_linkedin_sgo_pipeline_async_config

__all__ = (
    "LinkedInSGOPipelineConfigBundle",
    "ensure_required_paths",
    "ensure_sgo_namespace_defaults",
    "get_default_namespace",
    "load_linkedin_sgo_pipeline_async_config",
    "load_linkedin_sgo_pipeline_config",
)
