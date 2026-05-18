#!/usr/bin/env python3
"""
Curated **helper library** for ``tier1_delta_method_predictions.py``.

This module holds shared implementations for evolution state, ``memory/batch_analyses.json``,
group-summary refine/shrink, batch Part B root-cause memory, qualitative-summary seeding/sync,
``delta_method_predictions.json`` artifact writes, and ``TribeSchemaEvolver`` wiring.

**Runner:** production uses ``app.services.linkedin_sgo_pipeline_service_async``; this file is the dev/CLI mirror under ``scripts/scripts_sgo``.

**Full-file backup** (pre-truncation, for diff / audit): ``tier1_sgo_feedback_loop_FULL_BACKUP_before_helper_refactor.py``.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
from collections import OrderedDict
import importlib.util
import json
import logging
import os
import re
import shutil
import statistics
import sys
import time
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore

SCRIPTS_SGO = Path(__file__).resolve().parent.parent
REPO_ROOT = SCRIPTS_SGO.parent.parent
PROMPTS_DIR = SCRIPTS_SGO / "prompts"
if str(SCRIPTS_SGO) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_SGO))

if load_dotenv:
    load_dotenv(REPO_ROOT / ".env", override=True)

import _package_setup  # noqa: F401  # registers sgo_training.*

from openai import AsyncOpenAI

from calculate_behaviour_loss.weighted_topic_review_metric_calculation import (
    calculate_jsd,
    calculate_review_metrics,
)
from sgo_training.config import settings
from utils import io_utils, journey_utils
from utils.contextual_payload import load_contextual_payload
from utils.topic_presence_logprobs import process_single_post

from linkedin.i0_audience_room_helpers import (
    build_prediction_snapshot_for_memory,
    resolve_failed_prediction_for_memory_batch,
    run_audience_room_i0_for_post,
)
from linkedin.i0_linkedin_context import (
    CANONICAL_QUALITATIVE_SUMMARY_KEYS,
    format_qualitative_summary_descriptions_only,
    trait_section_heading_for_item,
)

from generate_synthetic_review_and_memory_analysis.prompt_generation_for_both_parts import (
    format_batch_analyses_memory_for_part_a,
    format_linkedin_group_memory_posts_section,
    generate_part_b_linkedin_group_memory_prompt,
)


def _load_tribe_schema_evolver_module():
    path = SCRIPTS_SGO / "sgo_training" / "tribe_schema_evolver.py"
    spec = importlib.util.spec_from_file_location("sgo_training.tribe_schema_evolver", path)
    if not spec or not spec.loader:
        raise ImportError(path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sgo_training.tribe_schema_evolver"] = mod
    spec.loader.exec_module(mod)
    return mod


_tse = _load_tribe_schema_evolver_module()
TribeSchemaEvolver = _tse.TribeSchemaEvolver
persona_dict_for_prompt_evolver = _tse.persona_dict_for_prompt
qualitative_slice_from_persona_response_evolver = _tse.qualitative_slice_from_persona_response
enforce_hard_limit_persona = _tse.enforce_hard_limit

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Persona evolution ceilings — shrink runs only when summary or traits exceed these.
DEFAULT_GROUP_SUMMARY_MAX_WORDS = 300
DEFAULT_MAX_TRAITS_PER_CATEGORY = 20

def _log_banner(msg: str, char: str = "=") -> None:
    """Same visual style as micro-cluster SGO: big ``====`` blocks on stdout + log."""
    line = char * 72
    print(line, flush=True)
    print(msg, flush=True)
    print(line, flush=True)
    logger.info(line)
    logger.info(msg)
    logger.info(line)


def _echo(fmt: str, *args: Any) -> None:
    """Micro-cluster parity: always print to stdout (flush) and log at INFO."""
    if args:
        text = fmt % args
        print(text, flush=True)
        logger.info(fmt, *args)
    else:
        print(fmt, flush=True)
        logger.info("%s", fmt)


def _warn(fmt: str, *args: Any) -> None:
    """Print + WARNING log so failures show in the terminal like the cluster pipeline."""
    if args:
        text = fmt % args
        print(text, flush=True)
        logger.warning(fmt, *args)
    else:
        print(fmt, flush=True)
        logger.warning("%s", fmt)


def load_group_summary_refine_template() -> str:
    """Same unified persona refine as ``TribeSchemaEvolver`` (``refine_characteristics.txt``)."""
    path = PROMPTS_DIR / "refine_characteristics.txt"
    if not path.is_file():
        raise FileNotFoundError(f"Missing prompt: {path}")
    return path.read_text(encoding="utf-8")


def load_group_summary_shrink_template() -> str:
    """Same unified shrink prompt as ``TribeSchemaEvolver.shrink_step`` (``shrink_refinement.txt``)."""
    path = PROMPTS_DIR / "shrink_refinement.txt"
    if not path.is_file():
        raise FileNotFoundError(f"Missing prompt: {path}")
    return path.read_text(encoding="utf-8")


def _trait_item_count_persona(q: Any) -> int:
    if not isinstance(q, dict):
        return 0
    n = 0
    for k in CANONICAL_QUALITATIVE_SUMMARY_KEYS:
        rows = q.get(k)
        if isinstance(rows, list):
            n += len(rows)
    return n


def iter_posts_with_ground_truth(
    payload: Dict[str, Any],
    *,
    tier_hint: str = "tier",
) -> List[Tuple[str, Dict[str, Any], Dict[str, Any]]]:
    """
    All posts under ``results_by_user`` that have non-empty ``topic_probabilities`` (ground-truth labels).

    Same iteration as :func:`iter_posts_tier1`; use ``tier_hint`` only for log context (for example ``tier1`` or ``tier2``).
    """
    src = (payload.get("source_file") or "").lower()
    if tier_hint and "tier_1" not in src and "tier1" not in src and "tier_2" not in src and "tier2" not in src:
        logger.warning(
            "[DATA] source_file=%r may not match expected %s stimulus; still using posts with GT.",
            src,
            tier_hint,
        )
    out: List[Tuple[str, Dict[str, Any], Dict[str, Any]]] = []
    by_user = payload.get("results_by_user") or {}
    for profile_id, bundle in by_user.items():
        if not isinstance(bundle, dict):
            continue
        for post in bundle.get("posts") or []:
            if not isinstance(post, dict) or not post.get("post_id"):
                continue
            tp = post.get("topic_probabilities")
            if not isinstance(tp, dict) or not tp:
                continue
            out.append((str(profile_id), bundle, post))
    return out


def iter_posts_tier1(payload: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any], Dict[str, Any]]]:
    """Backward-compatible name for :func:`iter_posts_with_ground_truth` (tier-1-shaped bundles)."""
    return iter_posts_with_ground_truth(payload, tier_hint="tier1")


def load_tribe_description(description_path: Path) -> Tuple[str, str]:
    if not description_path.is_file():
        raise FileNotFoundError(f"description.json not found: {description_path}")
    data = json.loads(description_path.read_text(encoding="utf-8"))
    summary = (data.get("summary") or "").strip()
    if not summary:
        raise ValueError(
            f"Non-empty 'summary' is required in {description_path} (audience / tribe characteristics)."
        )
    room = (data.get("audience_room_id") or "").strip()
    return summary, room


def load_tribe_description_from_dict(data: Dict[str, Any]) -> Tuple[str, str]:
    """Same as :func:`load_tribe_description` but from an already-loaded object (e.g. S3 fetch)."""
    summary = (data.get("summary") or "").strip()
    if not summary:
        raise ValueError(
            "Non-empty 'summary' is required in audience description JSON (tribe characteristics)."
        )
    room = (data.get("audience_room_id") or "").strip()
    return summary, room


def load_profile_json(profiles_root: Path, profile_id: str) -> Dict[str, Any]:
    path = profiles_root / profile_id / "profile.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("[PROFILE] Could not read %s: %s", path, e)
        return {}


def empty_qualitative_summary() -> Dict[str, Any]:
    # Canonical persona schema for this pipeline:
    # - 2 major sections overall: group_summary (stored separately in evolution state)
    #   and traits (stored here as qualitative_summary).
    # - Traits contains 5 subsections (lists). Each item is an object:
    #   { "text": str, "trait_section_title": str (optional), "rationalization": str, "confidence_score": float }
    return {
        "skills_and_expertise": [],
        "working_style": [],
        "motivations_and_values": [],
        "pain_points_and_needs": [],
        "org_leadership_and_psychographic_profile": [],
    }


def _canonical_trait_category_key(title: str) -> str:
    """Best-effort map from description.json trait block title → canonical 5-category key."""
    t = (title or "").strip().lower()
    if not t:
        return "org_leadership_and_psychographic_profile"
    if "skill" in t or "expert" in t:
        return "skills_and_expertise"
    if "working" in t or "work style" in t or "workflow" in t:
        return "working_style"
    if "motivation" in t or "value" in t:
        return "motivations_and_values"
    if "pain" in t or "need" in t:
        return "pain_points_and_needs"
    if "lead" in t or "psychographic" in t or "organiz" in t:
        return "org_leadership_and_psychographic_profile"
    # Fallback: keep unknown blocks in the broadest bucket.
    return "org_leadership_and_psychographic_profile"


def qualitative_summary_from_linkedin_traits(traits: Any) -> Optional[Dict[str, Any]]:
    """Map ``description.json`` ``traits`` blocks into the canonical 5-category ``qualitative_summary``.

    We ignore ``keywordTags`` entirely and seed from **descriptions** only. Each description bullet
    becomes its own trait item in the best-fit category.
    """
    if not isinstance(traits, list) or not traits:
        return None
    out = copy.deepcopy(empty_qualitative_summary())
    added = 0
    for block in traits:
        if not isinstance(block, dict):
            continue
        title = str(block.get("title") or "").strip()
        key = _canonical_trait_category_key(title)
        descs = block.get("descriptions") or []
        seg_list: List[str] = []
        if isinstance(descs, str):
            one = descs.strip()
            if one:
                seg_list = [one]
        elif isinstance(descs, list):
            seg_list = [str(d).strip() for d in descs if str(d).strip()]
        for seg in seg_list:
            out[key].append(
                {
                    "text": seg,
                    "trait_section_title": title,
                    "rationalization": "Seeded from LinkedIn audience-room description.json (traits descriptions).",
                    "confidence_score": 0.85,
                }
            )
            added += 1
    return out if added > 0 else None


def _extract_trait_text(tr: Any) -> str:
    if isinstance(tr, str):
        return tr.strip()
    if isinstance(tr, dict):
        t = (tr.get("text") or "").strip()
        if t:
            return t
    return ""


def linkedin_traits_from_qualitative_summary(q: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Export canonical 5-category ``qualitative_summary`` into ``description.json`` ``traits`` shape.

    We intentionally emit **no keywordTags** (empty list). Block ``title`` comes from each trait's
    ``trait_section_title`` when set (from audience-room ``traits[].title``); traits without it are
    grouped under the default heading for that canonical key.
    """
    out: List[Dict[str, Any]] = []
    for key in CANONICAL_QUALITATIVE_SUMMARY_KEYS:
        rows = q.get(key) if isinstance(q, dict) else None
        if not isinstance(rows, list) or not rows:
            continue
        groups: "OrderedDict[str, List[str]]" = OrderedDict()
        for tr in rows:
            text = _extract_trait_text(tr)
            if not text:
                continue
            sec_title = trait_section_heading_for_item(tr, key)
            groups.setdefault(sec_title, []).append(text)
        for sec_title, descs in groups.items():
            out.append({"title": sec_title, "keywordTags": [], "descriptions": descs})
    return out


def try_load_qualitative_from_description_json(description_path: Path) -> Dict[str, Any]:
    """Baseline persona JSON from ``description.json`` traits; empty schema if missing."""
    if not description_path.is_file():
        return empty_qualitative_summary()
    try:
        doc = json.loads(description_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("[DESCRIPTION] Could not read traits seed %s: %s", description_path, e)
        return empty_qualitative_summary()
    seeded = qualitative_summary_from_linkedin_traits(doc.get("traits"))
    return seeded if seeded else empty_qualitative_summary()


def ensure_workdir_description_from_seed(work_dir: Path, seed_path: Path) -> Path:
    """Copy ``seed_path`` (repo ``description.json``) into ``<work_dir>/description.json`` once.

    The **seed file is never modified** by the pipeline; all ``sync_description_json_from_state``
    updates go to the working copy under ``work_dir``.
    """
    work_dir = work_dir.resolve()
    seed_path = seed_path.resolve()
    dest = work_dir / "description.json"
    if dest.resolve() == seed_path:
        return dest
    work_dir.mkdir(parents=True, exist_ok=True)
    if dest.is_file():
        return dest
    if not seed_path.is_file():
        logger.error("[DESCRIPTION] seed file missing — cannot create working copy: %s", seed_path)
        return dest
    try:
        doc = json.loads(seed_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.error("[DESCRIPTION] cannot read seed for working copy: %s", e)
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(dest)
    logger.info("[DESCRIPTION] working copy (seed unchanged) → %s", dest)
    return dest


def sync_description_json_from_state(target_path: Path, state: Dict[str, Any]) -> None:
    """Write current ``group_summary`` / ``qualitative_summary`` into ``target_path`` (atomic).

    ``target_path`` should be the run-local ``<work_dir>/description.json`` from
    ``ensure_workdir_description_from_seed`` — not the read-only seed under ``raw_profiles/``.
    """
    if not target_path.is_file():
        logger.warning("[DESCRIPTION] skip sync — file missing: %s", target_path)
        return
    try:
        doc = json.loads(target_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.error("[DESCRIPTION] cannot read for sync: %s", e)
        return
    gs = (state.get("group_summary") or "").strip()
    if gs:
        doc["summary"] = gs
    traits = linkedin_traits_from_qualitative_summary(state.get("qualitative_summary") or {})
    if traits:
        doc["traits"] = traits
    tmp = target_path.with_suffix(target_path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(target_path)
    logger.info("[DESCRIPTION] synced summary + traits → %s", target_path)


def _ensure_scoring_mode_logprobs() -> None:
    if getattr(settings, "SCORING_MODE", None) in (None, ""):
        settings.SCORING_MODE = "logprobs"


def build_journey_log_from_user_predictions(user_predictions: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Same shape as ``tier1_delta_method_predictions.build_journey_log`` (cluster / journey tooling)."""
    log: Dict[str, Any] = {}
    for user_id, rows in user_predictions.items():
        if not isinstance(rows, list):
            continue
        for row in rows:
            rk = row.get("review_key")
            if not rk:
                continue
            ideltas = row.get("initial_prediction_deltas") or {}
            initial_for_journey = {
                "text": float(ideltas.get("text_delta", 0.0)),
                "theme": float(ideltas.get("theme_delta", 0.0)),
                "overall": float(ideltas.get("overall_delta", 0.0)),
            }
            review_entry = {
                "product_description": row.get("product_description", "N/A"),
                "category": row.get("category", "N/A"),
                "prediction": row.get("initial_prediction_data") or copy.deepcopy(row.get("prediction") or {}),
                "actual": row.get("actual") or {},
            }
            ent = journey_utils.create_journey_log_entry(
                rk,
                row.get("user_id", user_id),
                int(row.get("review_idx", 0)),
                review_entry,
                initial_for_journey,
                initial_prediction_data=row.get("initial_prediction_data"),
            )
            ent["correction_journey"] = copy.deepcopy(row.get("correction_journey") or [])
            log[rk] = ent
    return log


def _append_feedback_delta_row(
    rows: List[Dict[str, Any]],
    *,
    review_key: str,
    user_id: str,
    review_idx: int,
    post_id: str,
    iteration_label: str,
    text_delta: float,
    theme_delta: float,
    overall_delta: float,
    prediction: Dict[str, Any],
    actual: Dict[str, Any],
    category: str,
    product_description: str,
) -> None:
    scoring = getattr(settings, "SCORING_MODE", "confidence")
    rows.append(
        {
            "review_key": review_key,
            "user_id": user_id,
            "review_idx": review_idx,
            "post_id": post_id,
            "iteration_label": iteration_label,
            "text_delta": text_delta,
            "theme_delta": theme_delta,
            "theme_jsd": theme_delta if scoring in ("logprobs", "logprobs-without-persona-context") else None,
            "overall_delta": overall_delta,
            "prediction": copy.deepcopy(prediction),
            "actual": copy.deepcopy(actual),
            "category": category,
            "product_description": product_description,
        }
    )


def _flatten_user_predictions_map(by_user: Dict[str, Dict[int, Dict[str, Any]]]) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for uid, by_idx in by_user.items():
        if not isinstance(by_idx, dict) or not by_idx:
            continue
        out[uid] = [by_idx[i] for i in sorted(by_idx.keys())]
    return out


def _write_feedback_aggregate_deltas(work_dir: Path, user_predictions: Dict[str, List[Any]], rows: List[Any]) -> Path:
    work_dir.mkdir(parents=True, exist_ok=True)
    deltas_path = work_dir / "linkedin_tier1_all_reviews_deltas.json"
    io_utils.save_json_file(
        {
            "cluster_id": "linkedin",
            "micro_cluster_id": "tier1_feedback_loop",
            "total_reviews": sum(len(v) for v in user_predictions.values()),
            "total_delta_rows": len(rows),
            "calculation_timestamp": time.time(),
            "deltas": rows,
        },
        str(deltas_path),
    )
    return deltas_path


def write_delta_method_predictions_artifact(
    *,
    out_path: Path,
    user_pred_by_idx: Dict[str, Dict[int, Dict[str, Any]]],
    all_delta_rows: List[Dict[str, Any]],
    contextual_path: Path,
    description_work_path: Path,
    description_seed_path: Path,
    mapping_path: Optional[Path],
    work_dir: Path,
    state: Dict[str, Any],
    args: argparse.Namespace,
    model_type_used: Optional[str] = None,
    meta_extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Write ``delta_method_predictions.json``-compatible bundle (plus feedback-loop ``meta``)."""
    user_predictions = _flatten_user_predictions_map(user_pred_by_idx)
    jl = build_journey_log_from_user_predictions(user_predictions)
    wt = float(getattr(settings, "WEIGHT_TEXT_DELTA", 0.7))
    wm = float(getattr(settings, "WEIGHT_THEME_DELTA", 0.3))
    meta: Dict[str, Any] = {
            "contextual_json": str(contextual_path),
            "description_json": str(description_work_path),
            "description_seed_json": str(description_seed_path),
            "mapping_json": str(mapping_path) if mapping_path else None,
            "work_dir": str(work_dir),
            "scoring_mode": getattr(settings, "SCORING_MODE", None),
            "feedback_loop": True,
            "jsd_threshold": float(getattr(args, "jsd_threshold", 0.2)),
            "prediction_include_post_body": bool(getattr(args, "prediction_include_post_body", False)),
            "gt_prob_threshold_for_actual_theme_list": float(getattr(args, "gt_prob_threshold", 0.5)),
            "weight_text_delta": wt,
            "weight_theme_delta": wm,
            "total_delta_rows": len(all_delta_rows),
            "last_completed_iteration": state.get("last_completed_iteration"),
            "refinement_call_count": state.get("refinement_call_count"),
            "shrink_call_count": state.get("shrink_call_count"),
            "qual_refinement_call_count": state.get("qual_refinement_call_count"),
            "qual_shrink_call_count": state.get("qual_shrink_call_count"),
            "sync_description_json": getattr(args, "sync_description_json", True),
            "full_delta_post_pipeline": bool(getattr(args, "full_delta_post_pipeline", False)),
            "correction_iterations": getattr(args, "correction_iterations", None),
            "correction_delta_threshold": getattr(args, "correction_delta_threshold", None),
            "correction_feedback_on": getattr(args, "correction_feedback_on", None),
            "topic_model": getattr(args, "topic_model", None),
    }
    if meta_extra:
        meta.update(meta_extra)
    if str(description_work_path.resolve()) == str(description_seed_path.resolve()):
        meta.pop("description_seed_json", None)
    doc = {
        "model_type_used": model_type_used or "linkedin_tier1_feedback_loop",
        "user_predictions": user_predictions,
        "journey_log": jl,
        "meta": meta,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(out_path)
    logger.info("[DELTA_STYLE] wrote %s (%s users)", out_path, len(user_predictions))


_MEM_KEY_NEW = re.compile(r"^iteration_(\d+)_batch_(\d+)$")
_MEM_KEY_LEGACY = re.compile(r"^i(\d+)_batch_(\d+)$")


def linkedin_review_key(profile_id: str, post_id: Any) -> str:
    """Amazon-style id slot for memory['reviews'] (profile + post, not review index)."""
    return f"{profile_id}_post_{post_id}"


def memory_batch_key(iteration: int, batch_index_zero_based: int) -> str:
    """Match micro-cluster SGO keys, e.g. ``iteration_1_batch_2`` (batch is 1-based)."""
    return f"iteration_{iteration}_batch_{int(batch_index_zero_based) + 1}"


def sort_memory_batch_keys(keys: List[str]) -> List[str]:
    """Chronological order: ``iteration_*_batch_*`` then legacy ``i*_batch_*``; then unknown."""

    def sort_key(k: str) -> Tuple[int, int, int, str]:
        m = _MEM_KEY_NEW.match(k)
        if m:
            return (0, int(m.group(1)), int(m.group(2)), k)
        m = _MEM_KEY_LEGACY.match(k)
        if m:
            return (1, int(m.group(1)), int(m.group(2)), k)
        return (2, 99999, 99999, k)

    return sorted(keys, key=sort_key)


def merge_batch_logs_dict(
    full_memory: Dict[str, Any],
    window_logs: Dict[str, Any],
    max_history_batches: int,
) -> Dict[str, Any]:
    """Merge window + recent prior batches into one dict (for APIs that need batch-keyed logs)."""
    window_keys = set(window_logs.keys())
    prior_sorted = sort_memory_batch_keys([k for k in full_memory.keys() if k not in window_keys])
    if max_history_batches > 0:
        tail = prior_sorted[-max_history_batches :]
    else:
        tail = prior_sorted
    out: Dict[str, Any] = {k: full_memory[k] for k in tail}
    out.update(window_logs)
    return out


def format_individual_author_context(card: Dict[str, Any], bundle: Dict[str, Any]) -> str:
    blocks: List[str] = []
    summary = (card.get("summary") or "").strip()
    if summary:
        blocks.append(f"Individual profile summary:\n{summary}")
    headline = (card.get("headline") or card.get("jobTitle") or "").strip()
    if headline:
        blocks.append(f"LinkedIn headline: {headline}")
    hl = card.get("highlights")
    if isinstance(hl, list) and hl:
        blocks.append("Highlights: " + "; ".join(str(x) for x in hl[:25]))
    kw = card.get("keywords")
    if isinstance(kw, list) and kw:
        blocks.append("Keywords: " + ", ".join(str(x) for x in kw[:45]))
    if not blocks:
        pi = bundle.get("profile_info") if isinstance(bundle.get("profile_info"), dict) else {}
        occ = (pi.get("occupation") or pi.get("headline") or "").strip()
        if occ:
            blocks.append(f"[profile.json missing] profile_info only:\n{occ}")
        else:
            blocks.append("[profile.json missing] No individual summary — rely on post text.")
    return "\n\n".join(blocks)


def build_prediction_context(
    group_summary: str,
    qualitative_summary: Dict[str, Any],
    individual_author_context: str,
    stimulus: str,
    *,
    max_group_chars: int,
    max_individual_chars: int,
    max_qual_json_chars: int,
) -> str:
    gs = group_summary.strip()
    if len(gs) > max_group_chars:
        gs = gs[:max_group_chars] + "\n... [truncated]"
    ind = individual_author_context.strip()
    if len(ind) > max_individual_chars:
        ind = ind[:max_individual_chars] + "\n... [truncated]"
    q_prose = format_qualitative_summary_descriptions_only(qualitative_summary or {})
    if len(q_prose) > max_qual_json_chars:
        q_prose = q_prose[:max_qual_json_chars] + "\n... [truncated]"
    return (
        "=== Audience / tribe (evolving GROUP SUMMARY) ===\n"
        f"{gs}\n\n"
        "=== Group traits (evolving — description text only; no scores or rationalizations) ===\n"
        f"{q_prose}\n\n"
        "=== Individual author (profiles/<id>/profile.json) ===\n"
        f"{ind}\n\n"
        "=== Post stimulus (annotation) ===\n"
        f"{stimulus or 'None'}"
    )


async def delta_bridge_analysis(
    client: AsyncOpenAI,
    model: str,
    post_text: str,
    stimulus: str,
    category: str,
    group_summary_excerpt: str,
    qualitative_excerpt: str,
    individual_author_excerpt: str,
    topics_ordered: List[str],
    ground_truth: Dict[str, float],
    predicted: Dict[str, float],
    jsd: float,
) -> str:
    payload = {
        "evolving_group_summary_excerpt": group_summary_excerpt[:6000],
        "evolving_qualitative_persona_excerpt": (qualitative_excerpt or "")[:6000],
        "individual_author_context_excerpt": individual_author_excerpt[:6000],
        "stimulus": stimulus,
        "category": category,
        "post_text": post_text[:8000],
        "topics_ordered": topics_ordered,
        "ground_truth_topic_probabilities": ground_truth,
        "predicted_topic_probabilities": predicted,
        "jsd": jsd,
    }
    system = (
        "You analyze gaps between two topic-attention distributions for one LinkedIn post. "
        "Predictions used the evolving GROUP SUMMARY, trait **descriptions** (narrative text only), "
        "and individual author context. "
        "Return JSON: analysis (string), motives (array of short strings), where_wrong (string)."
    )
    user = json.dumps(payload, ensure_ascii=False, indent=2)
    resp = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
        temperature=0.25,
        max_tokens=1200,
        timeout=90.0,
    )
    raw = (resp.choices[0].message.content or "").strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    analysis = data.get("analysis") or ""
    motives = data.get("motives") or []
    where = data.get("where_wrong") or ""
    if isinstance(motives, list):
        mstr = "; ".join(str(m) for m in motives[:12])
    else:
        mstr = str(motives)
    return f"{analysis} | Where wrong: {where} | Motives: {mstr}"


def _word_count(s: str) -> int:
    return len(s.split())


def _trait_tokens(text: str) -> Set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2}


def _is_seeded_trait(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    rat = str(item.get("rationalization") or "").lower()
    return "seeded from linkedin audience-room" in rat


def _avg_trait_text_len(q: Any) -> float:
    if not isinstance(q, dict):
        return 0.0
    lens: List[int] = []
    for k in CANONICAL_QUALITATIVE_SUMMARY_KEYS:
        for row in q.get(k) or []:
            if isinstance(row, dict):
                lens.append(len(str(row.get("text") or "")))
    return (sum(lens) / len(lens)) if lens else 0.0


def qualitative_summary_regression(prior: Dict[str, Any], candidate: Dict[str, Any]) -> bool:
    """True when ``candidate`` is clearly worse than ``prior`` (shrink/refine guard)."""
    if not isinstance(candidate, dict) or _trait_item_count_persona(candidate) == 0:
        return True
    if _trait_item_count_persona(candidate) < _trait_item_count_persona(prior):
        return True
    prior_avg = _avg_trait_text_len(prior)
    cand_avg = _avg_trait_text_len(candidate)
    if prior_avg > 80 and cand_avg < prior_avg * 0.72:
        return True
    return False


def _best_trait_match_index(rows: List[Any], candidate_text: str) -> Optional[int]:
    cand_tok = _trait_tokens(candidate_text)
    if not cand_tok:
        return None
    best_i: Optional[int] = None
    best_score = 0.0
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        row_tok = _trait_tokens(str(row.get("text") or ""))
        if not row_tok:
            continue
        overlap = len(cand_tok & row_tok) / max(len(cand_tok | row_tok), 1)
        if overlap > best_score:
            best_score = overlap
            best_i = i
    return best_i if best_score >= 0.28 else None


def _looks_like_error_log_paraphrase(text: str) -> bool:
    """Block stubby meta-phrasing; allow conditional ``When …`` behavioral rules."""
    t = (text or "").strip().lower()
    if not t:
        return True
    if t.startswith("when ") and len(t) >= 60:
        return False
    return bool(
        re.match(r"^(emphasize|highlight|express|share|prioritize)\b", t)
        and len(t) < 120
    )


def _is_append_only_group_summary(baseline: str, candidate: str) -> bool:
    """True when candidate is baseline + a tacked-on tail sentence (weak refine)."""
    base = (baseline or "").strip()
    cand = (candidate or "").strip()
    if not base or not cand or len(cand) <= len(base) + 30:
        return False
    if not cand.startswith(base):
        return False
    tail = cand[len(base) :].strip().lower()
    return tail.startswith(("additionally,", "furthermore,", "in addition,", "also,"))


def normalize_group_summary_refinement(
    *,
    baseline: str,
    prior: str,
    candidate: str,
) -> str:
    """Reject append-only or no-op summary updates; keep integrated prose from prior/baseline."""
    base = (baseline or "").strip()
    prior_s = (prior or "").strip()
    cand = (candidate or "").strip()
    if not cand:
        return prior_s or base
    if _is_append_only_group_summary(base, cand) or _is_append_only_group_summary(prior_s, cand):
        logger.warning(
            "[REFINE] group_summary was append-only (%s words); keeping prior integrated text.",
            _word_count(cand),
        )
        return prior_s or base
    if prior_s and cand == prior_s:
        logger.warning("[REFINE] group_summary unchanged vs prior; keeping prior.")
        return prior_s
    if prior_s and len(cand) > 50:
        prior_w = set((prior_s or "").lower().split())
        cand_w = set(cand.lower().split())
        overlap = len(prior_w & cand_w) / max(len(prior_w | cand_w), 1)
        extra = len(cand) - len(prior_s)
        if overlap > 0.92 and extra < 80:
            logger.warning(
                "[REFINE] group_summary nearly identical to prior (overlap=%.2f, +%s chars); keeping prior.",
                overlap,
                extra,
            )
            return prior_s
    return cand


def memory_logs_for_refine(
    full_memory: Dict[str, Any],
    window_logs: Dict[str, Any],
    last_refined_batch_key: Optional[str],
) -> Dict[str, Any]:
    """All memory batches not yet consumed by a refine step, plus the current window."""
    keys = sort_memory_batch_keys(list(full_memory.keys()))
    start = 0
    if last_refined_batch_key:
        lk = str(last_refined_batch_key).strip()
        if lk in keys:
            start = keys.index(lk) + 1
    pending_keys = keys[start:]
    out: Dict[str, Any] = {k: full_memory[k] for k in pending_keys}
    out.update(window_logs)
    return out


def merge_qualitative_refinement(
    *,
    baseline: Dict[str, Any],
    prior: Dict[str, Any],
    candidate: Dict[str, Any],
    max_new_per_category: int = 5,
) -> Dict[str, Any]:
    """
    Merge LLM refine/shrink output into evolving traits without dropping audience-room baseline rows.

    - Baseline (seeded) traits are kept; matching candidate rows may enrich them.
    - Genuinely new, non-duplicate behaviors are appended (capped per category).
    """
    out = copy.deepcopy(prior if isinstance(prior, dict) else empty_qualitative_summary())
    base = baseline if isinstance(baseline, dict) else empty_qualitative_summary()
    cand = candidate if isinstance(candidate, dict) else empty_qualitative_summary()

    for key in CANONICAL_QUALITATIVE_SUMMARY_KEYS:
        base_rows = [r for r in (base.get(key) or []) if isinstance(r, dict)]
        merged: List[Dict[str, Any]] = copy.deepcopy([r for r in (out.get(key) or []) if isinstance(r, dict)])
        if not merged and base_rows:
            merged = copy.deepcopy(base_rows)

        seeded_texts = {
            str(r.get("text") or "").strip().lower()
            for r in merged
            if _is_seeded_trait(r) and str(r.get("text") or "").strip()
        }
        added_new = 0

        for row in cand.get(key) or []:
            if not isinstance(row, dict):
                continue
            text = str(row.get("text") or "").strip()
            if not text or text.lower() in seeded_texts:
                continue

            match_i = _best_trait_match_index(merged, text)
            if match_i is not None:
                cur = merged[match_i]
                cur_text = str(cur.get("text") or "").strip()
                if _is_seeded_trait(cur):
                    if not _looks_like_error_log_paraphrase(text):
                        if len(text) > len(cur_text) + 24:
                            cur["text"] = text
                        elif len(text) >= 40 and text.lower() not in cur_text.lower():
                            cur["text"] = f"{cur_text.rstrip()} — {text}"
                        if str(row.get("rationalization") or "").strip():
                            cur["rationalization"] = str(row["rationalization"]).strip()
                        try:
                            cur["confidence_score"] = max(
                                float(cur.get("confidence_score") or 0.0),
                                float(row.get("confidence_score") or 0.0),
                            )
                        except (TypeError, ValueError):
                            pass
                elif len(text) > len(cur_text):
                    merged[match_i] = {
                        **cur,
                        "text": text,
                        "rationalization": str(row.get("rationalization") or cur.get("rationalization") or "").strip()
                        or cur.get("rationalization"),
                        "confidence_score": row.get("confidence_score", cur.get("confidence_score")),
                        "trait_section_title": row.get("trait_section_title") or cur.get("trait_section_title"),
                    }
                continue

            if added_new >= max(0, int(max_new_per_category)):
                continue
            if _looks_like_error_log_paraphrase(text):
                continue
            if any(
                len(_trait_tokens(text) & _trait_tokens(str(r.get("text") or "")))
                / max(len(_trait_tokens(text) | _trait_tokens(str(r.get("text") or ""))), 1)
                > 0.55
                for r in merged
                if isinstance(r, dict)
            ):
                continue
            new_row = copy.deepcopy(row)
            if not str(new_row.get("trait_section_title") or "").strip():
                for br in base_rows:
                    if isinstance(br, dict) and br.get("trait_section_title"):
                        new_row["trait_section_title"] = br["trait_section_title"]
                        break
            merged.append(new_row)
            added_new += 1

        out[key] = merged

    enforce_hard_limit_preserve_seeded(out, max_items=DEFAULT_MAX_TRAITS_PER_CATEGORY)
    return out


def enforce_hard_limit_preserve_seeded(
    summary_block: Dict[str, Any],
    max_items: int = DEFAULT_MAX_TRAITS_PER_CATEGORY,
) -> Dict[str, Any]:
    """Like ``enforce_hard_limit_persona`` but never drops audience-room seeded baseline traits."""
    for k in CANONICAL_QUALITATIVE_SUMMARY_KEYS:
        items = summary_block.get(k, [])
        if not isinstance(items, list) or len(items) <= max_items:
            continue
        seeded: List[Tuple[float, Dict[str, Any]]] = []
        other: List[Tuple[float, Dict[str, Any]]] = []
        for item in items:
            if not isinstance(item, dict):
                other.append((0.0, item))
                continue
            score = float(item.get("confidence_score", 0.0) or 0.0)
            if _is_seeded_trait(item):
                seeded.append((score, item))
            else:
                other.append((score, item))
        other.sort(key=lambda x: x[0])
        keep_other = max(0, max_items - len(seeded))
        summary_block[k] = [item for _, item in seeded] + [item for _, item in other[-keep_other:]]
    return summary_block


def guard_group_summary_text(
    *,
    candidate: Optional[str],
    prior: str,
    baseline: str,
    max_words: int,
    min_words: int = 0,
) -> str:
    """Cap group_summary at ``max_words``; optional soft ``min_words`` only when > 0."""
    prior_s = (prior or "").strip()
    base_s = (baseline or "").strip()
    cand_s = (candidate or "").strip()
    if not cand_s:
        return prior_s or base_s
    mx = max(1, int(max_words))
    cw = _word_count(cand_s)
    mn = int(min_words)
    if mn > 0:
        pw = _word_count(prior_s)
        if cw < mn - 5 and (pw >= mn - 5 or cw < int(pw * 0.82)):
            logger.warning(
                "[REFINE] rejecting group_summary (%s words < floor %s); keeping prior (%s words).",
                cw,
                mn,
                pw,
            )
            return prior_s or base_s
    if cw > mx:
        return hard_truncate_words(cand_s, mx)
    return cand_s


def baseline_summary_min_words(
    baseline: str,
    *,
    floor_words: int = 200,
    floor_ratio: float = 0.90,
) -> int:
    """Soft minimum word target tied to audience-room baseline length."""
    bw = _word_count((baseline or "").strip())
    return max(int(floor_words), int(bw * float(floor_ratio))) if bw > 0 else int(floor_words)


def _baseline_anchor_spans(baseline: str) -> List[str]:
    """Concrete spans from baseline prose (comma/paren lists, em-dash clauses)."""
    import re

    spans: List[str] = []
    text = (baseline or "").strip()
    if not text:
        return spans
    for chunk in re.split(r"[—–]", text):
        chunk = chunk.strip(" .;")
        if len(chunk.split()) >= 4:
            spans.append(chunk)
    for m in re.finditer(r"\(([^)]{12,})\)", text):
        inner = m.group(1).strip()
        if len(inner.split()) >= 3:
            spans.append(inner)
    for part in re.split(r"[,;]", text):
        part = part.strip(" .")
        words = part.split()
        if 3 <= len(words) <= 14:
            spans.append(part)
    # Dedupe longest-first so substring hits do not double-count.
    uniq: List[str] = []
    seen: set[str] = set()
    for s in sorted(spans, key=len, reverse=True):
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(s)
    return uniq[:40]


def baseline_anchor_retention(baseline: str, text: str) -> float:
    """Fraction of baseline anchor spans still present in ``text`` (0–1)."""
    spans = _baseline_anchor_spans(baseline)
    if not spans:
        return 1.0
    hay = (text or "").lower()
    hits = sum(1 for s in spans if s.lower() in hay)
    return hits / len(spans)


def guard_shrink_group_summary(
    *,
    baseline: str,
    prior: str,
    candidate: Optional[str],
    max_words: int,
    min_words: int = 0,
    min_anchor_retention: float = 0.45,
) -> str:
    """Accept shrink output only if length and baseline specificity are preserved."""
    prior_s = (prior or "").strip()
    base_s = (baseline or "").strip()
    guarded = guard_group_summary_text(
        candidate=candidate,
        prior=prior_s,
        baseline=base_s,
        max_words=max_words,
        min_words=min_words,
    )
    if not base_s or guarded == prior_s:
        return guarded
    if baseline_anchor_retention(base_s, guarded) < float(min_anchor_retention):
        logger.warning(
            "[SHRINK] rejected summary — baseline anchor retention %.2f < %.2f; keeping prior (%s words).",
            baseline_anchor_retention(base_s, guarded),
            min_anchor_retention,
            _word_count(prior_s),
        )
        return prior_s or base_s
    return guarded


def should_run_group_shrink(
    group_summary: str,
    qualitative_summary: Dict[str, Any],
    *,
    max_words: int = DEFAULT_GROUP_SUMMARY_MAX_WORDS,
    max_traits_per_category: int = DEFAULT_MAX_TRAITS_PER_CATEGORY,
) -> bool:
    """True when traits exceed limit, or summary exceeds word limit (caller may hard-truncate summary-only)."""
    cw = _word_count(group_summary or "")
    mx = max(1, int(max_words))
    cap_t = max(1, int(max_traits_per_category))
    if cw > mx:
        return True
    for k in CANONICAL_QUALITATIVE_SUMMARY_KEYS:
        rows = qualitative_summary.get(k) if isinstance(qualitative_summary, dict) else None
        if isinstance(rows, list) and len(rows) > cap_t:
            return True
    return False


def qualitative_summary_over_trait_limit(
    qualitative_summary: Dict[str, Any],
    *,
    max_traits_per_category: int = DEFAULT_MAX_TRAITS_PER_CATEGORY,
) -> bool:
    cap_t = max(1, int(max_traits_per_category))
    if not isinstance(qualitative_summary, dict):
        return False
    return any(
        isinstance(qualitative_summary.get(k), list)
        and len(qualitative_summary[k]) > cap_t
        for k in CANONICAL_QUALITATIVE_SUMMARY_KEYS
    )


def round_prob_dict(d: Optional[Dict[str, Any]]) -> Dict[str, float]:
    """Stable floats for JSONL / stats (topic name -> probability)."""
    if not isinstance(d, dict):
        return {}
    out: Dict[str, float] = {}
    for k, v in d.items():
        try:
            out[str(k)] = round(float(v), 6)
        except (TypeError, ValueError):
            continue
    return out


def default_state(seed_group_summary: str) -> Dict[str, Any]:
    seed = (seed_group_summary or "").strip()
    eq = empty_qualitative_summary()
    return {
        "group_summary": seed,
        "baseline_group_summary": copy.deepcopy(seed),
        "qualitative_summary": copy.deepcopy(eq),
        "baseline_qualitative_summary": copy.deepcopy(eq),
        "refinement_call_count": 0,
        "shrink_call_count": 0,
        "qual_refinement_call_count": 0,
        "qual_shrink_call_count": 0,
        "batch_seq": 0,
        "last_completed_iteration": 0,
        "processed_post_ids": [],
        "last_refined_memory_batch_key": None,
    }


def migrate_legacy_state(state: Dict[str, Any], seed_group_summary: str) -> None:
    """If old evolution_state had qualitative_summary but no group_summary, seed from description."""
    if state.get("group_summary"):
        return
    logger.warning(
        "[STATE] No 'group_summary' key — initializing from description.json baseline "
        "(legacy qualitative_summary ignored)."
    )
    seed = (seed_group_summary or "").strip()
    state["group_summary"] = seed
    state["baseline_group_summary"] = copy.deepcopy(seed)


def normalize_qualitative_in_state(state: Dict[str, Any]) -> None:
    """Ensure qualitative_summary + baselines + counters exist."""
    if not isinstance(state.get("qualitative_summary"), dict):
        eq = empty_qualitative_summary()
        state["qualitative_summary"] = copy.deepcopy(eq)
        state["baseline_qualitative_summary"] = copy.deepcopy(eq)
    if not isinstance(state.get("baseline_qualitative_summary"), dict):
        state["baseline_qualitative_summary"] = copy.deepcopy(
            state.get("qualitative_summary") or empty_qualitative_summary()
        )
    if "qual_refinement_call_count" not in state:
        state["qual_refinement_call_count"] = 0
    if "qual_shrink_call_count" not in state:
        state["qual_shrink_call_count"] = 0
    if "last_refined_memory_batch_key" not in state:
        state["last_refined_memory_batch_key"] = None


def load_or_init_state(
    path: Path,
    seed_group_summary: str,
    initial_group_path: Optional[Path],
    description_path: Optional[Path] = None,
) -> Dict[str, Any]:
    if path.is_file():
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)
        migrate_legacy_state(state, seed_group_summary)
        if "shrink_call_count" not in state:
            state["shrink_call_count"] = 0
        normalize_qualitative_in_state(state)
        return state

    initial_text = (seed_group_summary or "").strip()
    if initial_group_path and initial_group_path.is_file():
        raw = initial_group_path.read_text(encoding="utf-8").strip()
        if raw.startswith("{"):
            try:
                obj = json.loads(raw)
                initial_text = (obj.get("summary") or initial_text).strip()
            except json.JSONDecodeError:
                logger.warning("[INIT] Could not parse %s as JSON; using raw text.", initial_group_path)
                initial_text = raw
        else:
            initial_text = raw or initial_text

    st = default_state(initial_text)
    if description_path:
        seeded = try_load_qualitative_from_description_json(description_path)
        n_tr = sum(
            len(seeded.get(k) or [])
            for k in (
                "skills_and_expertise",
                "working_style",
                "motivations_and_values",
                "pain_points_and_needs",
                "org_leadership_and_psychographic_profile",
            )
        )
        if n_tr > 0:
            st["qualitative_summary"] = copy.deepcopy(seeded)
            st["baseline_qualitative_summary"] = copy.deepcopy(seeded)
            logger.info(
                "[STATE] Seeded qualitative_summary from description.json (%s trait descriptions).", n_tr
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=2, ensure_ascii=False)
        f.write("\n")
    logger.info("[STATE] Created new evolution_state.json (group summary %s chars).", len(st["group_summary"]))
    return st


def save_state(path: Path, state: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
        f.write("\n")
    tmp.replace(path)


def next_state_snapshot_seq(history_dir: Path) -> int:
    """Highest ``NNNNNN_*.json`` prefix in ``history_dir`` so resumed runs continue numbering."""
    if not history_dir.is_dir():
        return 0
    best = 0
    for p in history_dir.glob("*.json"):
        m = re.match(r"^(\d{6})_", p.name)
        if m:
            best = max(best, int(m.group(1)))
    return best


def resolve_workdir_evolution_paths(work_dir: Path) -> Tuple[Path, Path]:
    """Return ``(evolution_state.json, snapshot_dir)`` under ``work_dir``.

    **Layout:** ``<work_dir>/evolution/evolution_state.json`` and ``<work_dir>/evolution/state_history/``.

    If only the legacy flat paths exist (``evolution_state.json`` and ``evolution_state_history/``
    at the root of ``work_dir``), they are **moved once** into ``evolution/`` so all evolution
    artifacts live together.
    """
    work_dir = work_dir.resolve()
    root = work_dir / "evolution"
    state_new = root / "evolution_state.json"
    hist_new = root / "state_history"
    state_old = work_dir / "evolution_state.json"
    hist_old = work_dir / "evolution_state_history"

    if state_new.is_file():
        hist_new.mkdir(parents=True, exist_ok=True)
        return state_new, hist_new

    root.mkdir(parents=True, exist_ok=True)

    if state_old.is_file():
        try:
            shutil.move(str(state_old), str(state_new))
        except OSError as e:
            logger.warning("[STATE] move evolution_state.json → evolution/ failed (%s); copying", e)
            shutil.copy2(state_old, state_new)
            try:
                state_old.unlink()
            except OSError:
                pass
        logger.info("[STATE] evolution_state.json → %s", state_new)

    if hist_old.is_dir():
        if not hist_new.exists():
            try:
                shutil.move(str(hist_old), str(hist_new))
            except OSError:
                hist_new.mkdir(parents=True, exist_ok=True)
                for item in hist_old.glob("*.json"):
                    dest = hist_new / item.name
                    if not dest.exists():
                        try:
                            shutil.move(str(item), str(dest))
                        except OSError:
                            shutil.copy2(item, dest)
                shutil.rmtree(hist_old, ignore_errors=True)
            logger.info("[STATE] evolution snapshots → %s", hist_new)
        else:
            hist_new.mkdir(parents=True, exist_ok=True)
            for item in hist_old.glob("*.json"):
                dest = hist_new / item.name
                if not dest.exists():
                    try:
                        shutil.move(str(item), str(dest))
                    except OSError:
                        shutil.copy2(item, dest)
            shutil.rmtree(hist_old, ignore_errors=True)
            logger.info("[STATE] merged legacy evolution_state_history/ → %s", hist_new)
    else:
        hist_new.mkdir(parents=True, exist_ok=True)

    return state_new, hist_new


def append_evolution_state_snapshot(
    history_dir: Path,
    state: Dict[str, Any],
    seq_holder: Dict[str, int],
    reason: str,
) -> Optional[Path]:
    """Write ``NNNNNN_<reason>.json`` copy (in addition to main ``evolution_state.json``)."""
    seq_holder["n"] = int(seq_holder.get("n", 0)) + 1
    n = seq_holder["n"]
    history_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w.\-]+", "_", (reason or "state")).strip("_")[:120] or "state"
    dest = history_dir / f"{n:06d}_{safe}.json"
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
        f.write("\n")
    tmp.replace(dest)
    return dest


def load_memory(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_memory(path: Path, mem: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(mem, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        return default


REFINE_ANALYSIS_LINE_MAX_CHARS = _int_env("REFINE_ANALYSIS_LINE_MAX_CHARS", 2800)
REFINE_ANALYSES_BLOB_MAX_CHARS = _int_env("REFINE_ANALYSES_BLOB_MAX_CHARS", 62000)


def _truncate_for_refine_prompt(text: str, max_len: int, *, what: str) -> str:
    s = (text or "").strip()
    if len(s) <= max_len:
        return s
    logger.warning(
        "[REFINE] truncating %s: %s chars → %s (keep head for LLM context limit)",
        what,
        len(s),
        max_len,
    )
    return s[: max_len - 40].rstrip() + "\n... [truncated for context limit]"


def format_batch_logs_for_prompt(
    batch_logs: Dict[str, Any],
    *,
    max_chars_per_analysis: int = REFINE_ANALYSIS_LINE_MAX_CHARS,
) -> str:
    """
    Human-readable blob for refine prompts. Expects Amazon-style entries:
    ``reviews``, ``analyses``, ``category`` (legacy ``post_ids`` still supported).
    """
    lines: List[str] = []
    cap_a = max(400, int(max_chars_per_analysis))
    for batch_key in sort_memory_batch_keys(list(batch_logs.keys())):
        data = batch_logs[batch_key]
        lines.append(f"--- {batch_key} ---")
        cat = (data.get("category") or "").strip()
        if cat:
            lines.append(f"category: {cat}")
        revs = data.get("reviews")
        if revs is None:
            revs = data.get("post_ids") or []
        if revs:
            lines.append("reviews: " + ", ".join(str(x) for x in revs))
        for a in data.get("analyses") or []:
            lines.append(f"- {_truncate_for_refine_prompt(str(a), cap_a, what='analysis line')}")
    return "\n".join(lines) if lines else "(no analyses this window)"


def build_refine_analyses_text(
    full_memory: Dict[str, Any],
    window_logs: Dict[str, Any],
    max_history_batches: int,
    *,
    window_only: bool = False,
    max_chars_per_analysis: int = REFINE_ANALYSIS_LINE_MAX_CHARS,
    max_total_chars: int = REFINE_ANALYSES_BLOB_MAX_CHARS,
) -> str:
    """
    Refine sees (1) the current refinement window (typically the last 5 memory batches)
    and optionally (2) a small tail of prior batches for dedupe/context.
    """
    parts: List[str] = []
    parts.append(
        "### CURRENT REFINEMENT WINDOW (last memory batches — primary evidence; "
        "rewrite group_summary from current persona + these analyses)\n"
    )
    parts.append(format_batch_logs_for_prompt(window_logs, max_chars_per_analysis=max_chars_per_analysis))
    if window_only:
        blob = "\n".join(parts)
        cap = max(8000, int(max_total_chars))
        if len(blob) > cap:
            blob = blob[: cap - 80].rstrip() + "\n\n... [MEMORY_BLOB_TRUNCATED]"
        return blob
    window_keys = set(window_logs.keys())
    prior_sorted = sort_memory_batch_keys([k for k in full_memory.keys() if k not in window_keys])
    if max_history_batches > 0 and prior_sorted:
        tail = prior_sorted[-max_history_batches :]
        hist = {k: full_memory[k] for k in tail}
        parts.append("\n### RECENT MEMORY (rolling context — avoid contradicting; dedupe themes)\n")
        parts.append(format_batch_logs_for_prompt(hist, max_chars_per_analysis=max_chars_per_analysis))
    blob = "\n".join(parts)
    cap = max(8000, int(max_total_chars))
    if len(blob) > cap:
        logger.warning(
            "[REFINE] analyses blob %s chars exceeds cap %s — truncating tail (recent memory dropped first at cut)",
            len(blob),
            cap,
        )
        blob = blob[: cap - 80].rstrip() + "\n\n... [MEMORY_BLOB_TAIL_TRUNCATED_FOR_CONTEXT_LIMIT]"
    return blob


def load_linkedin_memory_batch_root_cause_template() -> str:
    """LinkedIn batch memory uses ``prompts/part_b_memory_analysis.txt`` (group Part B)."""
    path = PROMPTS_DIR / "part_b_memory_analysis.txt"
    if not path.is_file():
        raise FileNotFoundError(f"Missing prompt: {path}")
    return path.read_text(encoding="utf-8")


def split_linkedin_memory_review_key(review_key: str) -> Tuple[Optional[str], Optional[str]]:
    """Parse ``{profile_id}_post_{post_id}`` (see ``linkedin_review_key``)."""
    s = str(review_key or "").strip()
    sep = "_post_"
    if sep not in s:
        return None, None
    i = s.index(sep)
    return s[:i], s[i + len(sep) :]


def memory_past_learnings_for_prompt(
    memory: Dict[str, Any],
    *,
    exclude_keys: Optional[Set[str]] = None,
    tail_batches: int = 6,
    max_chars: int = 12000,
) -> str:
    exclude_keys = exclude_keys or set()
    keys = [k for k in sort_memory_batch_keys(list(memory.keys())) if k not in exclude_keys]
    if not keys:
        return "(no prior memory batches yet)"
    tb = max(0, int(tail_batches))
    tail = keys[-tb:] if tb else keys
    sub = {k: memory[k] for k in tail}
    return _truncate_for_refine_prompt(
        format_batch_logs_for_prompt(sub),
        max_chars,
        what="past_learnings",
    )


async def linkedin_memory_batch_root_cause_analyses(
    client: AsyncOpenAI,
    model: str,
    *,
    state: Dict[str, Any],
    memory: Dict[str, Any],
    pending_memory_keys: Optional[Set[str]],
    review_items: List[Dict[str, Any]],
    fallback_analyses: List[str],
) -> List[str]:
    """
    One LinkedIn-specific batch call: root-cause strings for ``memory/batch_analyses.json`` ``analyses``.
    Uses ``part_b_memory_analysis.txt`` (group summary + traits + past learnings + posts section).
    Pipeline assigns ``reviews`` by position (same order as ``review_items``); LLM returns ``analyses`` only.
    Falls back to ``fallback_analyses`` (per-post delta notes) on any failure or invalid slot.
    """
    n = len(review_items)
    if n == 0:
        return []
    fb = list(fallback_analyses[:n])
    while len(fb) < n:
        fb.append("")
    expected_review_keys = [
        str(it.get("review_key") or "") if isinstance(it, dict) else ""
        for it in review_items
    ]
    past = memory_past_learnings_for_prompt(
        memory,
        exclude_keys=pending_memory_keys or set(),
        tail_batches=6,
        max_chars=12000,
    )
    slim: List[Dict[str, Any]] = []
    for it in review_items:
        if not isinstance(it, dict):
            slim.append({})
            continue
        slim.append(
            {
                "review_key": str(it.get("review_key") or ""),
                "category": str(it.get("category") or ""),
                "stimulus": _truncate_for_refine_prompt(str(it.get("stimulus") or ""), 4000, what="stimulus"),
                "actual_review_text": _truncate_for_refine_prompt(
                    str(it.get("actual_review_text") or ""), 8000, what="actual_body"
                ),
                "failed_prediction_text": _truncate_for_refine_prompt(
                    str(it.get("failed_prediction_text") or ""), 8000, what="pred_body"
                ),
                "topics_ordered": it.get("topics_ordered") or [],
                "predicted_themes": it.get("predicted_themes") or {},
                "ground_truth_topic_probabilities": it.get("ground_truth_topic_probabilities") or {},
                "preliminary_delta_notes": _truncate_for_refine_prompt(
                    str(it.get("preliminary_delta_notes") or ""), 6000, what="prelim_delta"
                ),
            }
        )
    posts_section = format_linkedin_group_memory_posts_section(slim)
    gs_part_b = _truncate_for_refine_prompt(
        (state.get("group_summary") or "").strip(),
        8000,
        what="part_b_group_summary",
    )
    q_state = state.get("qualitative_summary") or {}
    group_traits_str = _truncate_for_refine_prompt(
        format_qualitative_summary_descriptions_only(q_state),
        16000,
        what="part_b_group_traits",
    )

    try:
        load_linkedin_memory_batch_root_cause_template()
    except FileNotFoundError as e:
        logger.error("[MEMORY_RC] %s", e)
        return fb

    try:
        full_prompt = generate_part_b_linkedin_group_memory_prompt(
            group_summary=gs_part_b,
            group_traits=group_traits_str,
            past_learnings=past,
            posts_section=posts_section,
        )
    except FileNotFoundError as e:
        logger.error("[MEMORY_RC] prompt build failed: %s", e)
        return fb

    if len(full_prompt) > 115000:
        full_prompt = full_prompt[:115000] + "\n... [PROMPT_TRUNCATED]"

    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": full_prompt}],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=4096,
            timeout=180.0,
        )
        raw = (resp.choices[0].message.content or "").strip()
        data = json.loads(raw)
        rv_llm = data.get("reviews")
        if isinstance(rv_llm, list):
            norm_llm = [str(x).strip() for x in rv_llm]
            exp = expected_review_keys[:n]
            if norm_llm != exp:
                logger.warning(
                    "[MEMORY_RC] ignoring JSON reviews field (expected pipeline keys); "
                    "got_len=%s expected_len=%s match=%s",
                    len(norm_llm),
                    len(exp),
                    norm_llm == exp,
                )
        arr = data.get("analyses")
        if not isinstance(arr, list):
            logger.warning("[MEMORY_RC] analyses is not a list — using preliminary deltas")
            return fb
        if len(arr) != n:
            logger.warning(
                "[MEMORY_RC] expected analyses length %s, got %s — merging slots with fallbacks",
                n,
                len(arr),
            )
        out: List[str] = []
        for i in range(n):
            row = arr[i] if i < len(arr) else None
            s = str(row).strip() if row is not None else ""
            if len(s) >= 12 and "[" in s and "]" in s:
                out.append(s)
            else:
                out.append(fb[i])
        logger.info("[MEMORY_RC] wrote %s root-cause memory lines (batch)", len(out))
        return out
    except Exception as e:
        logger.error("[MEMORY_RC] batch LLM failed: %s", e)
        return fb


def hard_truncate_words(text: str, max_words: int) -> str:
    words = text.split()
    if max_words <= 0 or len(words) <= max_words:
        return text.strip()
    return " ".join(words[:max_words]).rstrip() + " …"


def compute_group_summary_shrink_band(
    current_text: str,
    baseline_text: str,
    *,
    floor_words: int,
    hard_max_words: int,
    baseline_ceiling_ratio: float,
    baseline_floor_ratio: float,
) -> Tuple[int, int]:
    """
    Word-count band [min, max] for group_summary relative to **baseline** prose length.

    Prevents overshrinking: ``min`` tracks baseline × floor ratio so shrink/refine prompts
    can ask the model to **enrich** thin summaries back toward audience-room depth.
    """
    bw = max(_word_count(baseline_text), 1)
    max_w = int(bw * baseline_ceiling_ratio)
    max_w = min(max(max_w, floor_words), hard_max_words)
    min_w = int(bw * baseline_floor_ratio)
    min_w = min(min_w, max_w)
    min_w = max(min_w, min(floor_words, max_w))
    return min_w, max_w


def compute_shrink_target_words(
    current_text: str,
    baseline_text: str,
    *,
    floor_words: int,
    hard_max_words: int,
    baseline_ceiling_ratio: float,
    baseline_floor_ratio: float = 0.88,
) -> int:
    """Backward-compatible ceiling: upper bound of :func:`compute_group_summary_shrink_band`."""
    _mn, mx = compute_group_summary_shrink_band(
        current_text,
        baseline_text,
        floor_words=floor_words,
        hard_max_words=hard_max_words,
        baseline_ceiling_ratio=baseline_ceiling_ratio,
        baseline_floor_ratio=baseline_floor_ratio,
    )
    return mx


async def refine_group_summary(
    client: AsyncOpenAI,
    model: str,
    current_summary: str,
    baseline_summary: str,
    full_memory: Dict[str, Any],
    window_logs: Dict[str, Any],
    history_batches: int,
    qualitative_summary: Dict[str, Any],
    baseline_qualitative_summary: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """
    Unified persona refine via ``refine_characteristics.txt``: same contract as qualitative refine.

    Returns ``(group_summary, qualitative_summary_or_none)``. The trait dict is ``None`` if the
    model response has no usable trait lists (caller may fall back to ``TribeSchemaEvolver``).
    """
    analyses_blob = build_refine_analyses_text(
        full_memory,
        window_logs,
        history_batches,
        window_only=True,
    )
    template = load_group_summary_refine_template()
    persona = persona_dict_for_prompt_evolver(current_summary, qualitative_summary)
    persona_json = json.dumps(persona, ensure_ascii=False, indent=2)
    if len(persona_json) > 28000:
        persona_json = persona_json[:27960].rstrip() + '\n... [truncated persona JSON]'
    full_prompt = template.format(
        current_group_persona_json=persona_json,
        batch_analyses_text=analyses_blob,
    )
    baseline_anchor = _truncate_for_refine_prompt(
        (baseline_summary or "").strip(),
        12000,
        what="baseline_group_summary_anchor",
    )
    cw = _word_count(current_summary)
    nw_batches = len(window_logs)
    full_prompt += (
        "\n\n### RUNTIME — REFINE CONTRACT (pipeline)\n"
        f"- **Current `group_summary` word count:** {int(cw)} — your output must be a **full rewrite** "
        f"integrating this text + traits + the **{int(nw_batches)} memory batch(es)** in CURRENT REFINEMENT WINDOW.\n"
        "- **Do NOT** return the input summary unchanged or with only an appended closing sentence.\n"
        "- **Preserve** named orgs, platforms, cohort contrasts, and technical domains from the current "
        "summary and baseline anchor unless error logs supersede them.\n"
        "- **Traits:** enrich existing bullets in place when error logs add nuance; add new conditional "
        "`When …` rules when genuinely distinct (max 10 new traits total).\n\n"
        "### RUNTIME — ORIGINAL AUDIENCE-ROOM BASELINE SUMMARY (anchor for specificity)\n"
        f"{baseline_anchor}\n"
    )
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": full_prompt}],
            response_format={"type": "json_object"},
            temperature=0.25,
            max_tokens=6144,
            timeout=120.0,
        )
        raw = (resp.choices[0].message.content or "").strip()
        data = json.loads(raw)
        new_s = (data.get("group_summary") or data.get("summary") or "").strip()
        new_qual = qualitative_slice_from_persona_response_evolver(data)
        enforce_hard_limit_preserve_seeded(new_qual, max_items=DEFAULT_MAX_TRAITS_PER_CATEGORY)
        if _trait_item_count_persona(new_qual) == 0:
            new_qual = None
        elif baseline_qualitative_summary is not None:
            new_qual = merge_qualitative_refinement(
                baseline=baseline_qualitative_summary,
                prior=qualitative_summary,
                candidate=new_qual,
                max_new_per_category=5,
            )
        if new_s:
            new_s = normalize_group_summary_refinement(
                baseline=baseline_summary,
                prior=current_summary,
                candidate=new_s,
            )
            new_s = guard_group_summary_text(
                candidate=new_s,
                prior=current_summary,
                baseline=baseline_summary,
                max_words=DEFAULT_GROUP_SUMMARY_MAX_WORDS,
            )
        return (new_s if new_s else None, new_qual)
    except Exception as e:
        logger.error("[REFINE] unified persona refine LLM failed: %s", e)
        return None, None


async def shrink_group_summary(
    client: AsyncOpenAI,
    model: str,
    text: str,
    baseline: str,
    min_summary_words: int,
    max_summary_words: int,
    qualitative_summary: Dict[str, Any],
) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """
    Unified shrink via ``shrink_refinement.txt``: returns ``(group_summary, qualitative_summary)``.
    Trait dict is ``None`` when the model omits usable trait lists (caller keeps prior state).
    """
    template = load_group_summary_shrink_template()
    persona = persona_dict_for_prompt_evolver(text, qualitative_summary)
    prompt = template.format(
        current_group_persona_json=json.dumps(persona, ensure_ascii=False, indent=2),
    )
    cw = _word_count(text)
    bw = _word_count(baseline)
    mn = max(1, int(min_summary_words))
    mx = max(mn, int(max_summary_words))
    prompt += (
        "\n\n### RUNTIME CONSTRAINTS (pipeline)\n"
        f"- **`group_summary` word-count band (strict):** **{mn}** words minimum, **{mx}** words maximum\n"
        f"- **Current `group_summary` word count:** {int(cw)}\n"
        f"- **Baseline `group_summary` word count (anchor length):** {int(bw)}\n"
        "- If current is **below** the minimum, **enrich** using trait lists + baseline anchor — "
        "do not invent new organizations or behaviors.\n"
        "- **Baseline group summary (anchor for scope, named entities, and cohort contrasts):**\n"
        f"{baseline.strip()}\n"
    )
    prev_trait_n = _trait_item_count_persona(qualitative_summary)
    for attempt in range(2):
        try:
            resp = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.15 if attempt == 0 else 0.05,
                max_tokens=6144,
                timeout=120.0,
            )
            raw = (resp.choices[0].message.content or "").strip()
            data = json.loads(raw)
            new_s = (data.get("group_summary") or data.get("summary") or "").strip()
            if not new_s:
                continue
            nw = _word_count(new_s)
            if nw > mx + 15:
                logger.warning(
                    "[SHRINK] model returned %s words > ceiling %s; hard-truncating.",
                    nw,
                    mx,
                )
                new_s = hard_truncate_words(new_s, mx)
            elif nw < mn - 5:
                logger.warning(
                    "[SHRINK] model returned %s words < floor %s — rejecting shrink summary.",
                    nw,
                    mn,
                )
                return None, None
            new_qual = qualitative_slice_from_persona_response_evolver(data)
            enforce_hard_limit_preserve_seeded(new_qual, max_items=DEFAULT_MAX_TRAITS_PER_CATEGORY)
            if _trait_item_count_persona(new_qual) == 0:
                if prev_trait_n > 0:
                    logger.warning(
                        "[SHRINK] model returned empty traits; keeping prior qualitative_summary."
                    )
                new_qual = None
            return new_s.strip(), new_qual
        except Exception as e:
            logger.error("[SHRINK] attempt %s failed: %s", attempt + 1, e)
    return None, None


async def run(args: argparse.Namespace) -> None:
    """
    Deprecated runner.

    `tier1_delta_method_predictions.py` is the single runner for the pipeline.
    This module is kept as a curated helper library for that runner.
    """
    raise RuntimeError(
        "tier1_sgo_feedback_loop.py is a helper module. "
        "Run scripts/scripts_sgo/linkedin/tier1_delta_method_predictions.py instead."
    )

