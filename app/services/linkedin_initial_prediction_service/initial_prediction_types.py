"""Types and artifact helpers for LinkedIn initial prediction."""

from __future__ import annotations

import copy
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PIPELINE_ID = "linkedin_initial_prediction"


def resolve_openai_api_key_for_tier(tier: int) -> str:
    tier = int(tier)
    if tier == 2:
        k = os.environ.get("OPENAI_API_KEY_TIER2") or os.environ.get(
            "OPENAI_API_KEY_INITIAL_PREDICTION_TIER2", ""
        )
    else:
        k = os.environ.get("OPENAI_API_KEY_TIER1") or os.environ.get(
            "OPENAI_API_KEY_INITIAL_PREDICTION_TIER1", ""
        )
    k = (k or "").strip()
    if k:
        return k
    return (os.environ.get("OPENAI_API_KEY") or "").strip()


def _post_key_to_meta_for_posts(
    posts: List[Tuple[str, Dict[str, Any], Dict[str, Any]]],
) -> Dict[Tuple[str, str], Tuple[int, str]]:
    seq: Dict[str, int] = {}
    out: Dict[Tuple[str, str], Tuple[int, str]] = {}
    for pid, _b, post in posts:
        post_id = str(post.get("post_id") or "")
        if not post_id:
            continue
        key = (str(pid), post_id)
        if key not in out:
            idx = seq.get(str(pid), 0)
            out[key] = (idx, f"{str(pid)}_review_{idx}")
            seq[str(pid)] = idx + 1
    return out


def _flatten_user_predictions(
    by_user: Dict[str, Dict[int, Dict[str, Any]]],
) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for uid, by_idx in by_user.items():
        if not isinstance(by_idx, dict) or not by_idx:
            continue
        out[uid] = [by_idx[i] for i in sorted(by_idx.keys())]
    return out


def slim_initial_prediction_record(entry: Dict[str, Any]) -> Dict[str, Any]:
    stim = entry.get("product_description") or ""
    init_d = entry.get("initial_deltas") or entry.get("initial_prediction_deltas") or {}
    tc = entry.get("i0_topic_classification")
    return {
        "review_key": entry.get("review_key"),
        "user_id": entry.get("user_id"),
        "review_idx": entry.get("review_idx"),
        "status": entry.get("status"),
        "stimulus": stim,
        "category": entry.get("category"),
        "post_id": entry.get("post_id"),
        "prediction": copy.deepcopy(entry.get("prediction")),
        "actual": copy.deepcopy(entry.get("actual")),
        "initial_deltas": copy.deepcopy(init_d),
        "i0_topic_classification": copy.deepcopy(tc) if tc is not None else {},
    }


def write_prediction_json_local(
    json_text: str,
    out_path: Optional[Path],
    *,
    mirror_repo_predictions_path: Optional[Path] = None,
) -> None:
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json_text, encoding="utf-8")
    if mirror_repo_predictions_path is not None:
        mirror_repo_predictions_path.parent.mkdir(parents=True, exist_ok=True)
        mirror_repo_predictions_path.write_text(json_text, encoding="utf-8")


def build_initial_prediction_artifact(
    *,
    tier: int,
    gen_model: str,
    topic_model: str,
    gt_prob_threshold: float,
    weight_text: float,
    weight_theme: float,
    checkpoint_every: int,
    contextual_label: str,
    description_label: str,
    mapping_label: str,
    profiles_root: Path,
    model_type_used: str,
    user_pred_by_idx: Dict[str, Dict[int, Dict[str, Any]]],
    errors: List[Dict[str, Any]],
    extra_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    flat_raw = _flatten_user_predictions(user_pred_by_idx)
    flat = {
        uid: [slim_initial_prediction_record(row) for row in rows]
        for uid, rows in flat_raw.items()
    }
    meta: Dict[str, Any] = {
        "tier": int(tier),
        "contextual_json": contextual_label,
        "description_json": description_label,
        "mapping_json": mapping_label,
        "profiles_root": str(profiles_root),
        "gen_model": gen_model,
        "topic_model": topic_model,
        "gt_prob_threshold": gt_prob_threshold,
        "weight_text": weight_text,
        "weight_theme": weight_theme,
        "checkpoint_every": int(checkpoint_every),
    }
    if extra_meta:
        meta.update(extra_meta)
    return {
        "model_type_used": model_type_used,
        "pipeline": PIPELINE_ID,
        "pipeline_step": "initial_prediction",
        "tier": int(tier),
        "calculation_timestamp": time.time(),
        "meta": meta,
        "user_predictions": flat,
        "errors": errors,
    }


@dataclass
class InitialPredictionRunResult:
    artifact: Dict[str, Any]
    output_path_written: Optional[Path]
    s3_key: Optional[str]
    s3_url: Optional[str]
    partial_s3_key: Optional[str]
    posts_attempted: int
    posts_ok: int
    posts_failed: int


@dataclass
class InitialPredictionRunParams:
    tier: int = 1
    contextual_payload: Dict[str, Any] = field(default_factory=dict)
    description_seed_path: Path = field(default_factory=Path)
    mapping_path: Path = field(default_factory=Path)
    profiles_root: Path = field(default_factory=Path)
    output_path: Optional[Path] = None
    mirror_repo_predictions_path: Optional[Path] = None
    gen_model: str = "gpt-4o-mini"
    topic_model: str = "gpt-4o-mini"
    model_type_used: str = PIPELINE_ID
    gt_prob_threshold: float = 0.5
    weight_text: Optional[float] = None
    weight_theme: Optional[float] = None
    scoring_mode: Optional[str] = None
    embedding_model: Optional[str] = None
    embedding_min_interval_sec: Optional[float] = None
    checkpoint_every: int = 5
    concurrent: int = 8
    max_posts: int = 0
    prob_threshold: float = 0.5
    max_retries: int = 3
    retry_delay: float = 1.0
    max_text_chars: int = 12000
    print_seed_context: bool = True
    no_initial_i0_prompt: bool = False
    print_every_i0_prompt: bool = False
    debug_i0_prompt: bool = False
    s3_upload: bool = False
    s3_partial_upload: bool = True
    audience_room_id: Optional[str] = None
    enterprise_name: Optional[str] = None
    source: Optional[str] = None
