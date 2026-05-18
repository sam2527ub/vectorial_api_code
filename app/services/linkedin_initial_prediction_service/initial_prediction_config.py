"""Load :file:`initial_prediction_config.yaml`."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Dict, Optional

import yaml

_LOCK = RLock()
_CFG: Optional["InitialPredictionConfig"] = None


@dataclass(frozen=True)
class InitialPredictionBatchConfig:
    posts_per_process_invocation: int
    save_partial_after_each_chunk: bool


@dataclass(frozen=True)
class InitialPredictionConfig:
    default_gen_model: str
    default_topic_model: str
    max_concurrent_posts: int
    checkpoint_every: int
    prob_threshold: float
    gt_prob_threshold: float
    max_retries: int
    retry_delay_sec: float
    max_text_chars: int
    scoring_mode: str
    weight_text_delta: float
    weight_theme_delta: float
    embedding_model: str
    embedding_min_interval_sec: float
    batch: InitialPredictionBatchConfig
    config_path: str = ""

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "default_gen_model": self.default_gen_model,
            "default_topic_model": self.default_topic_model,
            "max_concurrent_posts": self.max_concurrent_posts,
            "checkpoint_every": self.checkpoint_every,
            "prob_threshold": self.prob_threshold,
            "gt_prob_threshold": self.gt_prob_threshold,
            "max_retries": self.max_retries,
            "retry_delay_sec": self.retry_delay_sec,
            "max_text_chars": self.max_text_chars,
            "scoring_mode": self.scoring_mode,
            "weight_text_delta": self.weight_text_delta,
            "weight_theme_delta": self.weight_theme_delta,
            "embedding_model": self.embedding_model,
            "embedding_min_interval_sec": self.embedding_min_interval_sec,
            "batch": {
                "posts_per_process_invocation": self.batch.posts_per_process_invocation,
                "save_partial_after_each_chunk": self.batch.save_partial_after_each_chunk,
            },
        }


def _config_path() -> Path:
    env = os.environ.get("INITIAL_PREDICTION_CONFIG", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    try:
        from app.runtime_settings import pipeline_config_path

        rp = pipeline_config_path("linkedin_initial_prediction")
        if rp is not None and rp.is_file():
            return rp
    except Exception:
        pass
    return Path(__file__).resolve().parent / "initial_prediction_config.yaml"


def load_initial_prediction_config(force_reload: bool = False) -> InitialPredictionConfig:
    global _CFG
    with _LOCK:
        path = _config_path()
        if not force_reload and _CFG is not None and getattr(_CFG, "config_path", "") == str(path):
            return _CFG
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        batch_raw = raw.get("batch") or {}
        _CFG = InitialPredictionConfig(
            default_gen_model=str(raw.get("default_gen_model", "gpt-4o-mini")).strip(),
            default_topic_model=str(raw.get("default_topic_model", "gpt-4o-mini")).strip(),
            max_concurrent_posts=max(1, int(raw.get("max_concurrent_posts", 8))),
            checkpoint_every=max(0, int(raw.get("checkpoint_every", 5))),
            prob_threshold=float(raw.get("prob_threshold", 0.5)),
            gt_prob_threshold=float(raw.get("gt_prob_threshold", 0.5)),
            max_retries=max(0, int(raw.get("max_retries", 3))),
            retry_delay_sec=float(raw.get("retry_delay_sec", 1.0)),
            max_text_chars=int(raw.get("max_text_chars", 12000)),
            scoring_mode=str(raw.get("scoring_mode", "logprobs")).strip() or "logprobs",
            weight_text_delta=float(raw.get("weight_text_delta", 0.5)),
            weight_theme_delta=float(raw.get("weight_theme_delta", 0.5)),
            embedding_model=str(raw.get("embedding_model", "text-embedding-3-small")).strip(),
            embedding_min_interval_sec=float(raw.get("embedding_min_interval_sec", 0.05)),
            batch=InitialPredictionBatchConfig(
                posts_per_process_invocation=max(
                    1, int(batch_raw.get("posts_per_process_invocation", 4))
                ),
                save_partial_after_each_chunk=bool(
                    batch_raw.get("save_partial_after_each_chunk", True)
                ),
            ),
            config_path=str(path),
        )
        return _CFG


def get_initial_prediction_config() -> InitialPredictionConfig:
    return load_initial_prediction_config()
