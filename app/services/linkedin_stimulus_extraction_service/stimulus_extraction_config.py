"""Load `stimulus_extraction_config.yaml` (no hardcoded hyperparameters in callers)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional

import yaml

_LOCK = RLock()
_CFG: Optional["StimulusExtractionConfig"] = None
_CFG_PATH: Optional[Path] = None


@dataclass(frozen=True)
class TierStimulusExtraction:
    completion_max_tokens: int
    batches_per_process_invocation: int
    max_concurrency: int


@dataclass(frozen=True)
class StimulusExtractionConfig:
    default_model: str
    batch_size: int
    llm_temperature: float
    response_format: Dict[str, str]
    price_input_per_1m: float
    price_output_per_1m: float
    tier1: TierStimulusExtraction
    tier2: TierStimulusExtraction
    failed_post_ids_sample_max: int
    config_path: str = ""

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "default_model": self.default_model,
            "batch_size": self.batch_size,
            "llm_temperature": self.llm_temperature,
            "response_format": self.response_format,
            "tier1": {
                "completion_max_tokens": self.tier1.completion_max_tokens,
                "batches_per_process_invocation": self.tier1.batches_per_process_invocation,
                "max_concurrency": self.tier1.max_concurrency,
            },
            "tier2": {
                "completion_max_tokens": self.tier2.completion_max_tokens,
                "batches_per_process_invocation": self.tier2.batches_per_process_invocation,
                "max_concurrency": self.tier2.max_concurrency,
            },
            "failed_post_ids_sample_max": self.failed_post_ids_sample_max,
            "config_path": self.config_path,
        }


def _config_path() -> Path:
    env = os.environ.get("STIMULUS_EXTRACTION_CONFIG", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    try:
        from app.runtime_settings import pipeline_config_path

        rp = pipeline_config_path("linkedin_stimulus_extraction")
        if rp is not None and rp.is_file():
            return rp
    except Exception:
        pass
    return Path(__file__).resolve().parent / "stimulus_extraction_config.yaml"


def _coerce_tier1(raw: Dict[str, Any]) -> TierStimulusExtraction:
    t = raw.get("tier1") or {}
    return TierStimulusExtraction(
        completion_max_tokens=int(t.get("completion_max_tokens", 4096)),
        batches_per_process_invocation=int(t.get("batches_per_process_invocation", 1)),
        max_concurrency=int(t.get("max_concurrency", 3)),
    )


def _coerce_tier2(raw: Dict[str, Any]) -> TierStimulusExtraction:
    t = raw.get("tier2") or {}
    return TierStimulusExtraction(
        completion_max_tokens=int(t.get("completion_max_tokens", 4096)),
        batches_per_process_invocation=int(t.get("batches_per_process_invocation", 1)),
        max_concurrency=int(t.get("max_concurrency", 3)),
    )


def load_stimulus_extraction_config(force_reload: bool = False) -> StimulusExtractionConfig:
    global _CFG, _CFG_PATH
    with _LOCK:
        path = _config_path()
        if _CFG is not None and not force_reload and path == _CFG_PATH:
            return _CFG

        if not path.is_file():
            raise FileNotFoundError(f"stimulus_extraction config not found: {path}")
        with path.open("r", encoding="utf-8") as f:
            raw: Dict[str, Any] = yaml.safe_load(f) or {}

        llm = raw.get("llm") or {}
        rf = llm.get("response_format") or {"type": "json_object"}
        if "type" in rf and isinstance(rf["type"], str) and "{" not in rf["type"]:
            rf = {"type": rf["type"]}

        pricing = raw.get("pricing") or {}
        t1 = _coerce_tier1(raw)
        t2 = _coerce_tier2(raw)
        for tier in (t1, t2):
            if tier.batches_per_process_invocation < 1:
                raise ValueError("batches_per_process_invocation must be >= 1")
            if tier.max_concurrency < 1:
                raise ValueError("max_concurrency must be >= 1")

        _CFG = StimulusExtractionConfig(
            default_model=str(raw.get("default_model", "gpt-4o")),
            batch_size=int(raw.get("batch_size", 5)),
            llm_temperature=float(llm.get("temperature", 0.25)),
            response_format=rf,
            price_input_per_1m=float(pricing.get("price_input_per_1m", 0.15)),
            price_output_per_1m=float(pricing.get("price_output_per_1m", 0.60)),
            tier1=t1,
            tier2=t2,
            failed_post_ids_sample_max=int(raw.get("failed_post_ids_sample_max", 50)),
            config_path=str(path),
        )
        _CFG_PATH = path
        return _CFG


def get_stimulus_extraction_config() -> StimulusExtractionConfig:
    return load_stimulus_extraction_config()
