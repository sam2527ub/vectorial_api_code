"""Load :file:`ground_truth_extraction_config.yaml`."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Dict, Optional

import yaml

_LOCK = RLock()
_CFG: Optional["GroundTruthExtractionConfig"] = None
_CFG_PATH: Optional[Path] = None


@dataclass(frozen=True)
class GroundTruthTierRetry:
    max_attempts: int
    base_delay_sec: float
    max_delay_sec: float
    jitter_sec: float


@dataclass(frozen=True)
class GroundTruthBatchConfig:
    posts_per_process_invocation: int
    save_partial_after_each_chunk: bool


@dataclass(frozen=True)
class GroundTruthExtractionConfig:
    default_model: str
    max_concurrent_llm: int
    max_concurrent_posts: int
    prob_threshold: float
    max_text_chars: int
    per_call_timeout_sec: float
    client_total_timeout_sec: float
    include_post_body: bool
    batch: GroundTruthBatchConfig
    tier1: GroundTruthTierRetry
    tier2: GroundTruthTierRetry
    config_path: str = ""

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "default_model": self.default_model,
            "max_concurrent_llm": self.max_concurrent_llm,
            "max_concurrent_posts": self.max_concurrent_posts,
            "topic_llm_scheduling": "sequential_topics_parallel_posts",
            "prob_threshold": self.prob_threshold,
            "max_text_chars": self.max_text_chars,
            "per_call_timeout_sec": self.per_call_timeout_sec,
            "client_total_timeout_sec": self.client_total_timeout_sec,
            "include_post_body": self.include_post_body,
            "batch": {
                "posts_per_process_invocation": self.batch.posts_per_process_invocation,
                "save_partial_after_each_chunk": self.batch.save_partial_after_each_chunk,
            },
        }


def _config_path() -> Path:
    env = os.environ.get("GROUND_TRUTH_EXTRACTION_CONFIG", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    try:
        from app.runtime_settings import pipeline_config_path

        rp = pipeline_config_path("linkedin_ground_truth_extraction")
        if rp is not None and rp.is_file():
            return rp
    except Exception:
        pass
    return Path(__file__).resolve().parent / "ground_truth_extraction_config.yaml"


def _coerce_tier(key: str, raw: Dict[str, Any]) -> GroundTruthTierRetry:
    t = raw.get(key) or {}
    return GroundTruthTierRetry(
        max_attempts=int(t.get("max_attempts", 5)),
        base_delay_sec=float(t.get("base_delay_sec", 1.0)),
        max_delay_sec=float(t.get("max_delay_sec", 60.0)),
        jitter_sec=float(t.get("jitter_sec", 0.35)),
    )


def _coerce_batch(raw: Dict[str, Any]) -> GroundTruthBatchConfig:
    b = raw.get("batch") or {}
    return GroundTruthBatchConfig(
        posts_per_process_invocation=max(1, int(b.get("posts_per_process_invocation", 2))),
        save_partial_after_each_chunk=bool(b.get("save_partial_after_each_chunk", True)),
    )


def load_ground_truth_extraction_config(force_reload: bool = False) -> GroundTruthExtractionConfig:
    global _CFG, _CFG_PATH
    with _LOCK:
        path = _config_path()
        if not force_reload and _CFG is not None and _CFG_PATH == path:
            return _CFG
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        llm = raw.get("llm") or {}
        _CFG = GroundTruthExtractionConfig(
            default_model=str(raw.get("default_model", "gpt-4o-mini")).strip(),
            max_concurrent_llm=int(raw.get("max_concurrent_llm", 24)),
            max_concurrent_posts=int(raw.get("max_concurrent_posts", 6)),
            prob_threshold=float(llm.get("prob_threshold", 0.5)),
            max_text_chars=int(llm.get("max_text_chars", 6000)),
            per_call_timeout_sec=float(llm.get("per_call_timeout_sec", 90.0)),
            client_total_timeout_sec=float(llm.get("client_total_timeout_sec", 180.0)),
            include_post_body=bool(llm.get("include_post_body", True)),
            batch=_coerce_batch(raw),
            tier1=_coerce_tier("tier1", raw),
            tier2=_coerce_tier("tier2", raw),
            config_path=str(path),
        )
        _CFG_PATH = path
        return _CFG


def get_ground_truth_extraction_config() -> GroundTruthExtractionConfig:
    return load_ground_truth_extraction_config()


get_post_topic_presence_config = get_ground_truth_extraction_config
load_post_topic_presence_config = load_ground_truth_extraction_config

# Back-compat names for SGO / older imports
PostTopicTierRetry = GroundTruthTierRetry
PostTopicPresenceConfig = GroundTruthExtractionConfig

__all__ = (
    "GroundTruthBatchConfig",
    "GroundTruthExtractionConfig",
    "GroundTruthTierRetry",
    "PostTopicPresenceConfig",
    "PostTopicTierRetry",
    "get_ground_truth_extraction_config",
    "get_post_topic_presence_config",
    "load_ground_truth_extraction_config",
    "load_post_topic_presence_config",
)
