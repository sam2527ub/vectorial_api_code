#!/usr/bin/env python3
"""
LinkedIn tier-1 **delta-method** pipeline (SGO training from **i1** onward — **no on-line i0**).

**Production:** use only ``app.services.linkedin_sgo_pipeline_service_async`` (S3 I/O + ``vendored_sgo/``).
Deployed workers must **never** import or depend on ``scripts/``. This file is a **local development mirror** only.

**Initial predictions (i0)** must exist in ``--precomputed-i0-json`` (e.g. from ``run_linkedin_i0_only.py``).
This script loads them, then runs the same outer iterations as before: sweep **1** loads i0 from disk for
every post and writes traces; **i1+** runs only on posts whose loaded **initial** gate delta
(``--feedback-on`` theme vs overall) exceeds ``--delta-threshold`` (default **0.2**). Sweeps **2+**
only re-score posts whose **post-correction** ``best_deltas`` from the **previous** sweep exceeded
``--next-sweep-min-delta`` (default **0.2**).

**Per post (after load):** full-vector **theme** delta (JSD on ``topic_probabilities`` in ``logprobs`` mode),
**text** delta via embeddings, **overall** delta from ``settings`` weights, **correction_journey** with the
same keys as batch correction, and **best_deltas** / **best_prediction_data** selection.

**Tier:** pass ``--tier 2`` (or explicit paths) for tier-2 GT + mapping + i0 under
``outputs/linkedin_tier2_sgo/``. Tier **1** remains the default.

**Mandatory run directory (``--work-dir``):** if empty, defaults to
``scripts_sgo/outputs/linkedin_tier{N}_sgo/default_run``. Each outer iteration (default **5** via
``--num-iterations``): **(1)** sweep **all** posts — **hydrate i0 from --precomputed-i0-json** (no LLM i0).
**(2)** **i1+:** posts whose **loaded i0** gate delta (``--feedback-on``) exceeds ``--delta-threshold`` are batched (``--memory-batch-size``,
default 5); each batch runs the delta feedback loop (i1..iN), delta analyses into traces, and memory
when the resolved gate exceeds ``--memory-jsd-threshold`` (default ``--next-sweep-min-delta``).
``memory/batch_analyses.json`` flushes when the buffer reaches ``--min-posts-for-memory-batch``; each flush
runs the LinkedIn **batch root-cause** memory prompt (``prompts/part_b_memory_analysis.txt``)
unless ``--no-linkedin-root-cause-memory`` is set (then preliminary per-post delta notes are stored as before).
**(3)** After ``--evolve-every-n-batches`` full memory batches, runs group_summary refine + shrink and
qualitative (traits) refine/shrink (same prompts as ``tier1_sgo_feedback_loop``). **(4)** persists
``evolution/evolution_state.json``, optional
``evolution/state_history/NNNNNN_<reason>.json`` snapshots, ``<work-dir>/description.json`` (working
copy; seed ``--description-json`` stays read-only when sync is on), ``post_traces.jsonl``,
``iteration_stats.jsonl``, ``linkedin_tier{N}_all_reviews_deltas.json``, ``delta_method_predictions.json``,
per-iteration ``delta_method_predictions_iteration_<n>.json``, and mirrors the latest bundle to
``--output``.

``review_key`` format: ``{profile_id}_review_{idx}`` for journey tooling.

Requires ``OPENAI_API_KEY``. If ``settings.SCORING_MODE`` is unset, this script sets it to
``logprobs`` so theme deltas use JSD (LinkedIn GT is probabilistic).
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import importlib.util
import json
import logging
import os
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
if str(SCRIPTS_SGO) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_SGO))

if load_dotenv:
    load_dotenv(REPO_ROOT / ".env", override=True)

import _package_setup  # noqa: F401

from openai import AsyncOpenAI, OpenAI

from calculate_behaviour_loss.weighted_topic_review_metric_calculation import calculate_jsd
from sgo_training.config import settings
from utils import io_utils
from utils import journey_utils

from linkedin.i0_audience_room_helpers import (
    append_all_reviews_delta,
    build_prediction_snapshot_for_memory,
    category_themes_for_post,
    llm_json,
    load_category_themes_map,
    measure_deltas,
    prediction_dict,
    resolve_failed_prediction_for_memory_batch,
    topics_on_body,
)
from linkedin.i0_linkedin_context import format_qualitative_summary_descriptions_only
from utils.contextual_payload import load_contextual_payload

from generate_synthetic_review_and_memory_analysis.prompt_generation_for_both_parts import (
    format_batch_analyses_memory_for_part_a,
    generate_part_a_linkedin_tier1_correction_prompt,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


def _ensure_scoring_mode() -> None:
    if getattr(settings, "SCORING_MODE", None) in (None, ""):
        settings.SCORING_MODE = "logprobs"
        logger.info("[CONFIG] SCORING_MODE was unset; forcing 'logprobs' for JSD theme deltas.")


_TIERED = REPO_ROOT / "scripts/LInkedin_Category_Topic_Extraction/tiered_posts_grouped"
_RAW_PROFILES = REPO_ROOT / "scripts/Linkedin_Posts_Extraction/raw_profiles"


def _default_paths(tier: int) -> Dict[str, Path]:
    """Tier-aware defaults (aligned with ``run_linkedin_initial_prediction_local.py``)."""
    if int(tier) == 2:
        work = SCRIPTS_SGO / "outputs" / "linkedin_tier2_sgo" / "default_run"
        return {
            "contextual": _TIERED / "ground_truth_extraction_tier2.json",
            "mapping": _TIERED / "discovered_category_topic_mapping_tier2.json",
            "work": work,
            "output": work / "delta_method_predictions.json",
            "precomputed_i0": work / "linkedin_i0_only.json",
            "model_type": "linkedin_tier2_delta_method",
        }
    work = SCRIPTS_SGO / "outputs" / "linkedin_tier1_sgo" / "default_run"
    return {
        "contextual": _TIERED / "contextual_stimulus_extraction_with_topics.json",
        "mapping": _TIERED / "discovered_category_topic_mapping.json",
        "work": work,
        "output": work / "delta_method_predictions.json",
        "precomputed_i0": REPO_ROOT / "Predictions" / "linkedin_i0_only.json",
        "model_type": "linkedin_tier1_delta_method",
    }


def _tier_pipeline_names(tier: int) -> Tuple[str, str, str]:
    label = f"tier{int(tier)}"
    base = f"linkedin_{label}_delta_method"
    return label, base, f"{base}_predictions"


def _load_precomputed_i0_index(path: Path) -> Dict[Tuple[str, int], Dict[str, Any]]:
    """Map (profile_id, review_idx) -> slim row from ``run_linkedin_i0_only`` JSON."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    up = doc.get("user_predictions") or {}
    out: Dict[Tuple[str, int], Dict[str, Any]] = {}
    for uid, rows in up.items():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                ridx = int(row.get("review_idx", 0))
            except (TypeError, ValueError):
                continue
            out[(str(uid), ridx)] = row
    return out


def _validate_precomputed_covers_posts(
    posts: List[Tuple[str, Dict[str, Any], Dict[str, Any]]],
    post_key_to_meta: Dict[Tuple[str, str], Tuple[int, str]],
    index: Dict[Tuple[str, int], Dict[str, Any]],
    path: Path,
) -> None:
    missing: List[Tuple[str, int]] = []
    for pid, _b, p in posts:
        _pk = (str(pid), str(p.get("post_id") or ""))
        ridx, _rkey = post_key_to_meta.get(_pk, (0, f"{pid}_review_0"))
        key = (str(pid), int(ridx))
        if key not in index:
            missing.append((str(pid), int(ridx)))
    if missing:
        logger.error(
            "[I0] %s post(s) missing from precomputed JSON %s — regenerate with run_linkedin_i0_only.py "
            "(same contextual payload / posts). First missing (profile_id, review_idx): %s",
            len(missing),
            path,
            missing[0],
        )
        sys.exit(1)


def _slim_row_to_pipeline_entry(
    slim: Dict[str, Any],
    *,
    weight_text: float,
    weight_theme: float,
) -> Dict[str, Any]:
    """Rebuild tier1 ``entry`` dict from slim i0 JSON (compatible with i1+ / journey tooling)."""
    pred = copy.deepcopy(slim.get("prediction") or {})
    init_d = copy.deepcopy(slim.get("initial_deltas") or slim.get("initial_prediction_deltas") or {})
    actual = copy.deepcopy(slim.get("actual") or {})
    stim = (slim.get("stimulus") or slim.get("product_description") or "").strip()
    i0_tc = copy.deepcopy(slim.get("i0_topic_classification") or {})
    td = float(init_d.get("text_delta", 0.0))
    thm = float(init_d.get("theme_delta", 0.0))
    ov = float(init_d.get("overall_delta", 0.0))
    scoring = str(getattr(settings, "SCORING_MODE", "confidence") or "confidence")
    deltas_block = {
        "text_delta": td,
        "theme_delta": thm,
        "overall_delta": ov,
        "theme_jsd": thm if scoring in ("logprobs", "logprobs-without-persona-context") else None,
        "delta_weights": {"text": weight_text, "theme": weight_theme},
    }
    return {
        "review_key": slim.get("review_key"),
        "user_id": slim.get("user_id"),
        "review_idx": int(slim.get("review_idx", 0)),
        "status": slim.get("status", "success"),
        "product_description": stim,
        "category": slim.get("category"),
        "post_id": slim.get("post_id"),
        "prediction": copy.deepcopy(pred),
        "initial_prediction_data": copy.deepcopy(pred),
        "actual": actual,
        "metrics": {},
        "deltas": deltas_block,
        "initial_prediction_deltas": copy.deepcopy(init_d),
        "initial_deltas": copy.deepcopy(init_d),
        "i0_topic_classification": copy.deepcopy(i0_tc),
        "correction_journey": [],
    }


def _load_tier1_module():
    path = Path(__file__).resolve().parent / "tier1_sgo_feedback_loop.py"
    spec = importlib.util.spec_from_file_location("tier1_sgo_feedback_loop", path)
    if not spec or not spec.loader:
        raise ImportError(path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


t1 = _load_tier1_module()

format_individual_author_context = t1.format_individual_author_context
empty_qualitative_summary = t1.empty_qualitative_summary
load_tribe_description = t1.load_tribe_description
try_load_qualitative_from_description_json = t1.try_load_qualitative_from_description_json
sync_description_json_from_state = t1.sync_description_json_from_state
ensure_workdir_description_from_seed = t1.ensure_workdir_description_from_seed
save_state = t1.save_state
delta_bridge_analysis = t1.delta_bridge_analysis
iter_posts_tier1 = t1.iter_posts_tier1
iter_posts_with_ground_truth = t1.iter_posts_with_ground_truth
load_or_init_state = t1.load_or_init_state
round_prob_dict = t1.round_prob_dict
write_delta_method_predictions_artifact = t1.write_delta_method_predictions_artifact
normalize_qualitative_in_state = t1.normalize_qualitative_in_state
load_memory = t1.load_memory
save_memory = t1.save_memory
memory_batch_key = t1.memory_batch_key
linkedin_review_key = t1.linkedin_review_key
refine_group_summary = t1.refine_group_summary
shrink_group_summary = t1.shrink_group_summary
compute_shrink_target_words = t1.compute_shrink_target_words
compute_group_summary_shrink_band = t1.compute_group_summary_shrink_band
hard_truncate_words = t1.hard_truncate_words
build_refine_analyses_text = t1.build_refine_analyses_text
merge_batch_logs_dict = t1.merge_batch_logs_dict
normalize_group_summary_refinement = t1.normalize_group_summary_refinement
sort_memory_batch_keys = t1.sort_memory_batch_keys
word_count_t1 = t1._word_count
qualitative_summary_over_trait_limit = t1.qualitative_summary_over_trait_limit
baseline_summary_min_words = t1.baseline_summary_min_words
guard_shrink_group_summary = t1.guard_shrink_group_summary
guard_group_summary_text = t1.guard_group_summary_text
merge_qualitative_refinement = t1.merge_qualitative_refinement
qualitative_summary_regression = t1.qualitative_summary_regression
DEFAULT_GROUP_SUMMARY_MAX_WORDS = t1.DEFAULT_GROUP_SUMMARY_MAX_WORDS
DEFAULT_MAX_TRAITS_PER_CATEGORY = t1.DEFAULT_MAX_TRAITS_PER_CATEGORY
TribeSchemaEvolver = t1.TribeSchemaEvolver
load_group_summary_refine_template = t1.load_group_summary_refine_template
load_group_summary_shrink_template = t1.load_group_summary_shrink_template
append_evolution_state_snapshot = t1.append_evolution_state_snapshot
next_state_snapshot_seq = t1.next_state_snapshot_seq
resolve_workdir_evolution_paths = t1.resolve_workdir_evolution_paths
load_profile_json = t1.load_profile_json
linkedin_memory_batch_root_cause_analyses = t1.linkedin_memory_batch_root_cause_analyses
load_linkedin_memory_batch_root_cause_template = t1.load_linkedin_memory_batch_root_cause_template
split_linkedin_memory_review_key = t1.split_linkedin_memory_review_key


def load_evolved_context(
    work_dir: Optional[Path],
    description_path: Path,
) -> Tuple[str, Dict[str, Any]]:
    """Load tribe context for predictions: prefer ``evolution_state.json`` when present, else ``description.json``."""
    seed_summary, _ = load_tribe_description(description_path)
    qual = try_load_qualitative_from_description_json(description_path)
    if work_dir:
        state_path, _ = resolve_workdir_evolution_paths(work_dir)
        if state_path.is_file():
            try:
                st = json.loads(state_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Could not load evolution_state.json: %s", e)
            else:
                seed_summary = (st.get("group_summary") or seed_summary).strip()
                if isinstance(st.get("qualitative_summary"), dict):
                    qual = copy.deepcopy(st["qualitative_summary"])
    return seed_summary, qual


def ensure_evolution_state_baseline_from_description(
    work_dir: Path,
    description_path: Path,
) -> None:
    """
    If ``work_dir/evolution/evolution_state.json`` is missing, create it from ``description.json`` baselines
    (same top-level keys as ``tier1_sgo_feedback_loop.default_state`` + trait seeding).

    Does **not** modify an existing file — evolved state from the feedback loop must not be
    overwritten by this single-pass prediction script.
    """
    path, _ = resolve_workdir_evolution_paths(work_dir)
    if path.is_file():
        return
    work_dir.mkdir(parents=True, exist_ok=True)
    seed_summary, _ = load_tribe_description(description_path)
    seed_summary = (seed_summary or "").strip()
    seeded = try_load_qualitative_from_description_json(description_path)
    if not isinstance(seeded, dict):
        seeded = copy.deepcopy(empty_qualitative_summary())
    else:
        n_seed = sum(
            len(seeded.get(k) or [])
            for k in (
                "skills_and_expertise",
                "working_style",
                "motivations_and_values",
                "pain_points_and_needs",
                "org_leadership_and_psychographic_profile",
            )
            if isinstance(seeded.get(k), list)
        )
        if n_seed == 0:
            seeded = copy.deepcopy(empty_qualitative_summary())
    st: Dict[str, Any] = {
        "group_summary": seed_summary,
        "baseline_group_summary": copy.deepcopy(seed_summary),
        "qualitative_summary": copy.deepcopy(seeded),
        "baseline_qualitative_summary": copy.deepcopy(seeded),
        "refinement_call_count": 0,
        "shrink_call_count": 0,
        "qual_refinement_call_count": 0,
        "qual_shrink_call_count": 0,
        "batch_seq": 0,
        "last_completed_iteration": 0,
        "processed_post_ids": [],
    }
    save_state(path, st)
    logger.info(
        "[STATE] created %s from description.json (baselines only; no memory/refine in this script)",
        path,
    )


def apply_select_best_prediction(entry: Dict[str, Any]) -> None:
    """Mirror ``select_best_prediction_by_theme_delta`` for one review dict (in-place)."""
    initial_deltas = entry.get("initial_prediction_deltas") or {}
    initial_data = entry.get("initial_prediction_data") or copy.deepcopy(entry.get("prediction") or {})
    if "overall_delta" not in initial_deltas or not initial_data:
        return

    best_overall = float(initial_deltas.get("overall_delta", 1.0))
    best_theme = float(initial_deltas.get("theme_delta", 1.0))
    best_prediction = copy.deepcopy(initial_data)

    for correction in entry.get("correction_journey") or []:
        co = correction.get("new_overall_delta")
        ct = correction.get("new_theme_delta")
        cpd = correction.get("corrected_prediction_data")
        if cpd is None:
            continue
        theme_improved = ct is not None and float(ct) < best_theme
        theme_same_overall = (
            ct is not None
            and abs(float(ct) - best_theme) < 0.001
            and co is not None
            and float(co) < best_overall
        )
        if theme_improved or theme_same_overall:
            best_overall = float(co) if co is not None else best_overall
            best_theme = float(ct) if ct is not None else best_theme
            best_prediction = copy.deepcopy(cpd)

    entry["best_deltas"] = {"overall_delta": best_overall, "theme_delta": best_theme}
    entry["best_prediction_data"] = best_prediction

    initial_overall = float(initial_deltas.get("overall_delta", 1.0))
    initial_theme = float(initial_deltas.get("theme_delta", 1.0))
    pred_initial = entry.get("prediction") or {}
    improved_theme = best_theme < initial_theme
    improved_same_theme = abs(best_theme - initial_theme) < 0.001 and best_overall < initial_overall
    if best_prediction != pred_initial and (improved_theme or improved_same_theme):
        entry["prediction"] = copy.deepcopy(best_prediction)


def build_journey_log(user_predictions: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Same shape as ``journey_utils.create_journey_log_entry`` + filled ``correction_journey``."""
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


def _write_aggregate_deltas_json(
    work_dir: Path,
    user_predictions: Dict[str, List[Dict[str, Any]]],
    all_delta_rows: List[Dict[str, Any]],
    *,
    tier: int,
) -> Path:
    work_dir.mkdir(parents=True, exist_ok=True)
    tier_label = f"tier{int(tier)}"
    deltas_path = work_dir / f"linkedin_{tier_label}_all_reviews_deltas.json"
    io_utils.save_json_file(
        {
            "cluster_id": "linkedin",
            "micro_cluster_id": tier_label,
            "total_reviews": sum(len(v) for v in user_predictions.values()),
            "total_delta_rows": len(all_delta_rows),
            "calculation_timestamp": time.time(),
            "deltas": all_delta_rows,
        },
        str(deltas_path),
    )
    return deltas_path


def _user_pred_by_idx_from_predictions_doc(doc: Dict[str, Any]) -> Dict[str, Dict[int, Dict[str, Any]]]:
    out: Dict[str, Dict[int, Dict[str, Any]]] = {}
    up = doc.get("user_predictions") or {}
    if not isinstance(up, dict):
        return out
    for uid, rows in up.items():
        if not isinstance(rows, list):
            continue
        inner: Dict[int, Dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                ridx = int(row.get("review_idx", 0))
            except (TypeError, ValueError):
                continue
            inner[ridx] = copy.deepcopy(row)
        if inner:
            out[str(uid)] = inner
    return out


def _qualifying_keys_from_predictions_doc(
    doc: Dict[str, Any],
    next_sweep_thr: float,
    feedback_on: str,
) -> Set[Tuple[str, str]]:
    keys: Set[Tuple[str, str]] = set()
    up = doc.get("user_predictions") or {}
    if not isinstance(up, dict):
        return keys
    for uid, rows in up.items():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            post_id = str(row.get("post_id") or "")
            if not post_id:
                continue
            g = _gate_delta_from_deltas(_resolved_deltas_for_gates(row), feedback_on)
            if g > next_sweep_thr:
                keys.add((str(uid), post_id))
    return keys


def _post_baseline_done_this_iteration(rec: Any, iteration: int) -> bool:
    return isinstance(rec, dict) and int(rec.get("feedback_loop_iteration") or 0) == int(iteration)


def _post_outer_iteration_complete(
    rec: Any,
    iteration: int,
    correction_iterations: int,
    delta_thr: float,
    feedback_on: str,
) -> bool:
    if not isinstance(rec, dict):
        return False
    if int(rec.get("feedback_loop_iteration") or 0) != int(iteration):
        return False
    journey = rec.get("correction_journey") or []
    if len(journey) >= int(correction_iterations):
        return True
    if journey and _gate_delta_from_deltas(_resolved_deltas_for_gates(rec), feedback_on) <= delta_thr:
        return True
    return False


def _infer_resume_start_iteration_from_stats(work_dir: Path, tier: int, num_iterations: int) -> Optional[int]:
    stats_path = work_dir / "iteration_stats.jsonl"
    if not stats_path.is_file():
        return None
    tier_label = f"tier{int(tier)}"
    pipeline_suffix = f"linkedin_{tier_label}_delta_method_predictions"
    completed: List[int] = []
    for line in stats_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        pipe = str(row.get("pipeline") or "")
        if row.get("tier") == int(tier) or pipe == pipeline_suffix:
            completed.append(int(row.get("iteration") or 0))
    if not completed:
        return None
    last_done = max(completed)
    if last_done >= int(num_iterations):
        return None
    partial = work_dir / "delta_method_predictions.json"
    if partial.is_file():
        return last_done + 1
    return last_done + 1


def _print_review_progress(
    posts_done: int,
    total_posts: int,
    review_key: str,
    rec: Dict[str, Any],
) -> None:
    init = rec.get("initial_prediction_deltas") or {}
    best = rec.get("best_deltas") or {}
    n_corr = len(rec.get("correction_journey") or [])
    cat = rec.get("category") or ""
    post_id = rec.get("post_id") or ""
    pred = rec.get("best_prediction_data") or rec.get("prediction") or {}
    pred_text = str(pred.get("review_text") or "").replace("\n", " ").strip()
    if len(pred_text) > 200:
        pred_text = pred_text[:200] + "…"
    block = (
        f"[REVIEW {posts_done}/{total_posts}] {review_key} post_id={post_id} category={cat!r}\n"
        f"  i0: text={init.get('text_delta')} theme={init.get('theme_delta')} "
        f"overall={init.get('overall_delta')} | corrections={n_corr} | "
        f"best: theme={best.get('theme_delta')} overall={best.get('overall_delta')}\n"
        f"  best_pred_text: {pred_text!r}"
    )
    print(block, flush=True)
    logger.info(
        "[REVIEW] %s | i0 t/th/o=%s/%s/%s corr=%s best t/o=%s/%s",
        review_key,
        init.get("text_delta"),
        init.get("theme_delta"),
        init.get("overall_delta"),
        n_corr,
        best.get("theme_delta"),
        best.get("overall_delta"),
    )


async def regenerate_post(
    client: AsyncOpenAI,
    model: str,
    sem: asyncio.Semaphore,
    *,
    group_summary: str,
    qualitative_summary: Dict[str, Any],
    individual_ctx: str,
    category: str,
    stimulus: str,
    prior_text: str,
    prior_topics: Dict[str, float],
    gt_probs: Dict[str, float],
    topics_ordered: List[str],
    analysis_used: str,
    past_learnings: str,
    max_group_chars: int,
    max_qual_json_chars: int,
    max_individual_chars: int,
) -> str:
    """
    Rewrites the synthetic post using ``part_a_individual_prompt_logprobs.txt`` (Part A correction).
    Response JSON: ``{"corrections": [{"post_text": "..."}]} `` (``review_text`` accepted as alias).
    """
    user_msg = generate_part_a_linkedin_tier1_correction_prompt(
        group_summary=group_summary,
        qualitative_summary=qualitative_summary,
        individual_ctx=individual_ctx,
        category=category,
        stimulus=stimulus,
        prior_text=prior_text,
        prior_topics=prior_topics,
        gt_probs=gt_probs,
        topics_ordered=topics_ordered,
        analysis_used=analysis_used,
        past_learnings=past_learnings,
        max_group_chars=max_group_chars,
        max_qual_json_chars=max_qual_json_chars,
        max_individual_chars=max_individual_chars,
    )
    system = (
        "You are a persona-based LinkedIn post prediction agent. Follow the user instructions exactly. "
        "Respond with ONLY a valid JSON object (no markdown code fences, no text before or after JSON) "
        'with key "corrections": an array of exactly ONE object containing '
        '"post_text" (string). Write the post body in first person as this person would on LinkedIn.'
    )
    data = await llm_json(client, model, system, user_msg, sem, max_tokens=4096)
    text = ""
    corrections = data.get("corrections")
    if isinstance(corrections, list) and corrections:
        first = corrections[0]
        if isinstance(first, dict):
            text = (first.get("post_text") or first.get("review_text") or "").strip()
    if not text:
        text = (data.get("post_text") or data.get("review_text") or "").strip()
    if not text:
        logger.warning("[CORRECTION] Part A response missing post_text; keeping prior draft")
        return (prior_text or "").strip()
    return text


async def run_correction_rounds_on_entry(
    entry: Dict[str, Any],
    *,
    iteration_cap: int,
    delta_threshold: float,
    weight_text: float,
    weight_theme: float,
    feedback_on: str,
    client: AsyncOpenAI,
    sync_client: OpenAI,
    gen_model: str,
    delta_model: str,
    topic_model: str,
    sem: asyncio.Semaphore,
    profiles_root: Path,
    group_summary: str,
    qualitative_summary: Dict[str, Any],
    profile_id: str,
    review_idx: int,
    review_key: str,
    bundle: Dict[str, Any],
    post: Dict[str, Any],
    emb_cache: Dict[str, Any],
    emb_lock: Lock,
    category_themes_list: Optional[List[str]],
    all_delta_rows: List[Dict[str, Any]],
    delta_rows_lock: Optional[asyncio.Lock] = None,
    prob_threshold: float,
    max_retries: int,
    retry_delay: float,
    max_text_chars: int,
    max_group_chars: int,
    max_individual_chars: int,
    max_qual_json_chars: int,
    delta_rows_iter_prefix: str = "",
    past_learnings: str = "",
) -> None:
    """Run i1..iN delta corrections on an existing i0 ``entry`` (mutates in place)."""
    if iteration_cap <= 0:
        return

    post_id = str(post.get("post_id"))
    topics = list(post.get("topics_ordered") or [])
    body = (post.get("body_text") or "").strip()
    stim = (post.get("stimulus") or "").strip()
    category = str(post.get("category") or "")
    gt_probs = {k: float(v) for k, v in (post.get("topic_probabilities") or {}).items()}
    theme_jsd_support = category_themes_list if category_themes_list else topics

    card = t1.load_profile_json(profiles_root, profile_id)
    individual_ctx = format_individual_author_context(card, bundle)
    group_summary = (group_summary or "").strip()
    qualitative_summary = qualitative_summary or empty_qualitative_summary()

    actual_block = {
        "review_text": body,
        "predicted_themes": gt_probs,
        "topic_probabilities": gt_probs,
    }

    pred_block = entry.get("prediction") or {}
    current_text = str(pred_block.get("review_text") or "")
    current_topics = {k: float(v) for k, v in (pred_block.get("predicted_themes") or {}).items()}

    id0 = entry.get("initial_prediction_deltas") or {}
    prev_theme = float(id0.get("theme_delta", 1.0))
    prev_overall = float(id0.get("overall_delta", 1.0))

    entry["correction_journey"] = []
    qual_excerpt = format_qualitative_summary_descriptions_only(qualitative_summary)
    if len(qual_excerpt) > max_qual_json_chars:
        qual_excerpt = qual_excerpt[:max_qual_json_chars]

    for it in range(1, iteration_cap + 1):
        if feedback_on == "overall":
            if prev_overall <= delta_threshold:
                logger.info(
                    "[DELTA] review_key=%s stop: overall_delta=%s <= %s",
                    review_key,
                    prev_overall,
                    delta_threshold,
                )
                break
        else:
            if prev_theme <= delta_threshold:
                logger.info(
                    "[DELTA] review_key=%s stop: theme_delta=%s <= %s (legacy)",
                    review_key,
                    prev_theme,
                    delta_threshold,
                )
                break

        analysis_raw = await delta_bridge_analysis(
            client,
            delta_model,
            body,
            stim,
            category,
            group_summary[:6000],
            qual_excerpt,
            individual_ctx,
            topics,
            gt_probs,
            current_topics,
            float(calculate_jsd(current_topics, gt_probs, theme_jsd_support)),
        )
        analysis_used = str(analysis_raw)

        current_text = await regenerate_post(
            client,
            gen_model,
            sem,
            group_summary=group_summary,
            qualitative_summary=qualitative_summary,
            individual_ctx=individual_ctx,
            category=category,
            stimulus=stim,
            prior_text=current_text,
            prior_topics=current_topics,
            gt_probs=gt_probs,
            topics_ordered=topics,
            analysis_used=analysis_used,
            past_learnings=past_learnings,
            max_group_chars=max_group_chars,
            max_qual_json_chars=max_qual_json_chars,
            max_individual_chars=max_individual_chars,
        )
        t_out2 = await topics_on_body(
            client,
            topic_model,
            sem,
            post,
            current_text,
            topics,
            prob_threshold,
            max_retries,
            retry_delay,
            max_text_chars,
        )
        current_topics = {k: float(v) for k, v in (t_out2.get("topic_probabilities") or {}).items()}

        n_td, n_thm, n_ov = await asyncio.to_thread(
            measure_deltas,
            review_key,
            current_text,
            body,
            current_topics,
            gt_probs,
            theme_jsd_support,
            sync_client,
            emb_cache,
            emb_lock,
            weight_text,
            weight_theme,
            f"iter{it}",
        )

        corrected = prediction_dict(current_text, current_topics)
        topic_logprobs_raw = copy.deepcopy(t_out2.get("topic_logprobs") or {})
        entry["correction_journey"].append(
            {
                "iteration": it,
                "corrected_prediction_data": copy.deepcopy(corrected),
                "new_text_delta": n_td,
                "new_theme_delta": n_thm,
                "new_overall_delta": n_ov,
                "analysis_used": analysis_used,
                "topic_logprobs": topic_logprobs_raw,
            }
        )

        _dlc = (
            f"{delta_rows_iter_prefix}correction_{it}"
            if delta_rows_iter_prefix
            else f"correction_{it}"
        )
        if delta_rows_lock is not None:
            async with delta_rows_lock:
                append_all_reviews_delta(
                    all_delta_rows,
                    review_key=review_key,
                    user_id=profile_id,
                    review_idx=review_idx,
                    post_id=post_id,
                    text_delta=n_td,
                    theme_delta=n_thm,
                    overall_delta=n_ov,
                    prediction=corrected,
                    actual=actual_block,
                    category=category,
                    product_description=stim,
                    iter_label=_dlc,
                )
        else:
            append_all_reviews_delta(
                all_delta_rows,
                review_key=review_key,
                user_id=profile_id,
                review_idx=review_idx,
                post_id=post_id,
                text_delta=n_td,
                theme_delta=n_thm,
                overall_delta=n_ov,
                prediction=corrected,
                actual=actual_block,
                category=category,
                product_description=stim,
                iter_label=_dlc,
            )

        prev_theme = n_thm
        prev_overall = n_ov


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


async def _memory_analysis_for_delta_entry(
    client: AsyncOpenAI,
    delta_model: str,
    max_qual_json_chars: int,
    entry: Dict[str, Any],
    post: Dict[str, Any],
    group_text: str,
    qual: Dict[str, Any],
    card: Dict[str, Any],
    bundle: Dict[str, Any],
) -> Optional[str]:
    journey = entry.get("correction_journey") or []
    parts = [
        str(s.get("analysis_used") or "").strip()
        for s in journey
        if isinstance(s, dict) and s.get("analysis_used")
    ]
    if parts:
        blob = "\n\n---\n\n".join(p for p in parts if p)
        return blob or None
    topics = list(post.get("topics_ordered") or [])
    if not topics:
        return None
    gt = {k: float(v) for k, v in (post.get("topic_probabilities") or {}).items()}
    pred_raw = (entry.get("initial_prediction_data") or {}).get("predicted_themes") or {}
    pred: Dict[str, float] = {}
    if isinstance(pred_raw, dict):
        for k, v in pred_raw.items():
            try:
                pred[str(k)] = float(v)
            except (TypeError, ValueError):
                continue
    jsd = float(calculate_jsd(pred, gt, topics))
    individual_ctx = format_individual_author_context(card, bundle)
    stim = (post.get("stimulus") or "").strip()
    body = (post.get("body_text") or "").strip()
    qual_excerpt = format_qualitative_summary_descriptions_only(qual)
    if len(qual_excerpt) > max_qual_json_chars:
        qual_excerpt = qual_excerpt[:max_qual_json_chars]
    return await delta_bridge_analysis(
        client,
        delta_model,
        body,
        stim,
        str(post.get("category") or ""),
        group_text,
        qual_excerpt,
        individual_ctx,
        topics,
        gt,
        pred,
        jsd,
    )


def _resolved_deltas_for_gates(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Deltas after i1..iN when present (``best_deltas``), else i0 ``initial_prediction_deltas``."""
    bd = rec.get("best_deltas")
    if isinstance(bd, dict) and bd:
        return bd
    id0 = rec.get("initial_prediction_deltas")
    return id0 if isinstance(id0, dict) else {}


def _gate_delta_from_deltas(deltas: Dict[str, Any], feedback_on: str) -> float:
    if feedback_on == "overall":
        return float(deltas.get("overall_delta", 0.0))
    return float(deltas.get("theme_delta", 0.0))


async def run_all(args: argparse.Namespace) -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        logger.error("OPENAI_API_KEY is not set")
        sys.exit(1)

    _ensure_scoring_mode()

    tier = int(getattr(args, "tier", 1) or 1)
    tier_label, pipeline_name, pipeline_predictions_name = _tier_pipeline_names(tier)
    tier_defs = _default_paths(tier)
    default_run_dir = tier_defs["work"].resolve()
    wd_raw = str(getattr(args, "work_dir", "") or "").strip()
    work_dir = Path(wd_raw).expanduser().resolve() if wd_raw else default_run_dir
    if not wd_raw:
        logger.info("[CONFIG] tier=%s empty --work-dir → using default %s", tier, work_dir)

    contextual_path = Path(args.contextual_json).expanduser().resolve()
    description_seed_path = Path(args.description_json).expanduser().resolve()
    mapping_path = Path(args.mapping_json).expanduser().resolve()
    profiles_root = Path(args.profiles_root).expanduser().resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.output).expanduser().resolve()

    weight_text = float(args.weight_text) if args.weight_text is not None else float(settings.WEIGHT_TEXT_DELTA)
    weight_theme = float(args.weight_theme) if args.weight_theme is not None else float(settings.WEIGHT_THEME_DELTA)
    delta_thr = float(args.delta_threshold) if args.delta_threshold is not None else float(settings.DELTA_THRESHOLD)
    next_sweep_thr = float(getattr(args, "next_sweep_min_delta", 0.2))
    memory_jsd_thr = (
        float(args.memory_jsd_threshold)
        if getattr(args, "memory_jsd_threshold", None) is not None
        else next_sweep_thr
    )

    setattr(args, "jsd_threshold", memory_jsd_thr)
    setattr(args, "correction_delta_threshold", delta_thr)
    setattr(args, "correction_feedback_on", args.feedback_on)
    setattr(args, "full_delta_post_pipeline", True)
    if not hasattr(args, "prediction_include_post_body"):
        setattr(args, "prediction_include_post_body", False)
    if not hasattr(args, "linkedin_root_cause_memory"):
        setattr(args, "linkedin_root_cause_memory", True)
    if not hasattr(args, "linkedin_root_cause_model"):
        setattr(args, "linkedin_root_cause_model", "")

    theme_map = load_category_themes_map(mapping_path)
    state_path, state_history_dir = resolve_workdir_evolution_paths(work_dir)
    memory_path = work_dir / "memory" / "batch_analyses.json"
    trace_path = work_dir / "post_traces.jsonl"
    iteration_stats_path = work_dir / "iteration_stats.jsonl"
    state_snap_seq: Dict[str, int] = {"n": next_state_snapshot_seq(state_history_dir)}

    description_work_path = ensure_workdir_description_from_seed(work_dir, description_seed_path)
    seed_group_summary, _ = load_tribe_description(description_seed_path)
    state = load_or_init_state(state_path, seed_group_summary, None, description_seed_path)
    memory = load_memory(memory_path)
    baseline_group = (state.get("baseline_group_summary") or seed_group_summary).strip()
    normalize_qualitative_in_state(state)

    payload = load_contextual_payload(contextual_path)
    if not str(payload.get("source_file") or "").strip():
        payload["source_file"] = contextual_path.name
    all_posts = iter_posts_with_ground_truth(payload, tier_hint=tier_label)
    if args.max_posts > 0:
        all_posts = all_posts[: args.max_posts]

    post_key_to_meta: Dict[Tuple[str, str], Tuple[int, str]] = _post_key_to_meta_for_posts(all_posts)
    post_bundle_by_key: Dict[Tuple[str, str], Tuple[Dict[str, Any], Dict[str, Any]]] = {}
    for pid, b, p in all_posts:
        _post_id = str(p.get("post_id") or "")
        if _post_id:
            post_bundle_by_key[(str(pid), _post_id)] = (b, p)

    pc_raw = str(getattr(args, "precomputed_i0_json", "") or "").strip()
    precomputed_i0_path = (
        Path(pc_raw).expanduser().resolve()
        if pc_raw
        else (REPO_ROOT / "Predictions" / "linkedin_i0_only.json")
    )
    if not precomputed_i0_path.is_file():
        logger.error(
            "[I0] Precomputed i0 JSON not found: %s — run scripts_sgo/linkedin/run_linkedin_i0_only.py first.",
            precomputed_i0_path,
        )
        sys.exit(1)
    precomputed_i0_index = _load_precomputed_i0_index(precomputed_i0_path)
    _validate_precomputed_covers_posts(all_posts, post_key_to_meta, precomputed_i0_index, precomputed_i0_path)
    logger.info(
        "[I0] Loaded %s reviews from %s (no on-line i0 in this script)",
        len(precomputed_i0_index),
        precomputed_i0_path,
    )

    logger.info(
        "[DATA] tier=%s contextual=%s | posts_with_GT=%s | work_dir=%s | evolution_state=%s",
        tier,
        contextual_path.name,
        len(all_posts),
        work_dir,
        state_path,
    )
    logger.info(
        "[PIPELINE] num_iterations=%s | next_sweep_min_delta=%s (post-correction best_deltas) | "
        "precomputed_i0=%s | i1 batch=%s (min_posts=%s) | evolve every %s memory batches | memory_gate=%s (%s) | correction_cap=%s",
        int(args.num_iterations),
        next_sweep_thr,
        precomputed_i0_path.name,
        int(args.memory_batch_size),
        int(args.min_posts_for_memory_batch),
        int(args.evolve_every_n_batches),
        memory_jsd_thr,
        args.feedback_on,
        int(args.correction_iterations),
    )

    base_url = os.environ.get("OPENAI_BASE_URL")
    client = AsyncOpenAI(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=base_url if base_url and base_url.strip() else None,
        timeout=120.0,
    )
    sync_client = OpenAI(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=base_url if base_url and base_url.strip() else None,
        timeout=120.0,
    )

    sem = asyncio.Semaphore(max(1, args.concurrent))
    emb_cache: Dict[str, Any] = {}
    emb_lock = Lock()
    delta_rows_lock = asyncio.Lock()
    trace_lock = asyncio.Lock()

    load_group_summary_refine_template()
    load_group_summary_shrink_template()

    if getattr(args, "linkedin_root_cause_memory", True):
        try:
            load_linkedin_memory_batch_root_cause_template()
        except FileNotFoundError as e:
            logger.error(
                "[MEMORY_RC] Missing batch memory prompt (required when --linkedin-root-cause-memory): %s",
                e,
            )
            sys.exit(1)

    persona_evolver: Optional[TribeSchemaEvolver] = None
    if not getattr(args, "disable_qual_schema", False):
        persona_evolver = TribeSchemaEvolver(
            cluster_id="linkedin",
            micro_id=f"{tier_label}_delta",
            memory_dir=str(work_dir / "memory"),
            refined_schema_dir=str(work_dir),
            evolution_model=args.qual_schema_model,
            refine_prompt_basename="refine_characteristics",
            shrink_prompt_basename="shrink_refinement",
        )

    def persist_state(reason: str, user_pred_by_idx: Dict[str, Dict[int, Dict[str, Any]]], rows: List[Dict[str, Any]]) -> None:
        save_state(state_path, state)
        # Sync work-dir description.json only when group/persona text actually changes (refine/shrink),
        # not after every memory batch or i0 chunk — same cadence as LLM refinement (every
        # --evolve-every-n-batches full memory batches, plus tail refine on last batch).
        sync_desc = getattr(args, "sync_description_json", True)
        sync_every_save = getattr(args, "sync_description_json_every_state_save", False)
        if sync_desc and (
            sync_every_save
            or reason.startswith("after_group_refine_")
            or reason.startswith("after_group_shrink_")
            or reason.startswith("after_qual_refine_")
            or reason.startswith("after_qual_shrink_")
        ):
            try:
                wd = ensure_workdir_description_from_seed(work_dir, description_seed_path)
                sync_description_json_from_state(wd, state)
                print(f"[DESCRIPTION] synced summary + traits → {wd}", flush=True)
            except OSError as e:
                logger.error("[DESCRIPTION] sync failed: %s", e)
        d_out = (
            Path(args.delta_style_predictions_output).expanduser().resolve()
            if str(getattr(args, "delta_style_predictions_output", "") or "").strip()
            else work_dir / "delta_method_predictions.json"
        )
        try:
            write_delta_method_predictions_artifact(
                out_path=d_out,
                user_pred_by_idx=user_pred_by_idx,
                all_delta_rows=rows,
                contextual_path=contextual_path,
                description_work_path=description_work_path,
                description_seed_path=description_seed_path,
                mapping_path=mapping_path,
                work_dir=work_dir,
                state=state,
                args=args,
                model_type_used=str(args.model_type_used),
                meta_extra={
                    "tier": tier,
                    "pipeline": f"{pipeline_predictions_name}_full",
                    "precomputed_i0_json": str(precomputed_i0_path),
                    "memory_jsd_threshold": memory_jsd_thr,
                    "next_sweep_min_delta": next_sweep_thr,
                    "outer_sweep_post_filter": "full_then_best_delta_gt_next_sweep_min",
                    "memory_batch_size": int(args.memory_batch_size),
                    "min_posts_for_memory_batch": int(args.min_posts_for_memory_batch),
                    "evolve_every_n_batches": int(args.evolve_every_n_batches),
                    "delta_style_predictions_output": str(d_out),
                },
            )
        except Exception as e:
            logger.exception("[DELTA_STYLE] artifact write failed: %s", e)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            tmp = out_path.with_suffix(out_path.suffix + ".tmp")
            tmp.write_text(d_out.read_text(encoding="utf-8"), encoding="utf-8")
            tmp.replace(out_path)
        except Exception as e:
            logger.warning("[OUTPUT] could not mirror delta bundle to --output: %s", e)
        if rows:
            flat = _flatten_user_predictions_for_aggregate(user_pred_by_idx)
            dp = _write_aggregate_deltas_json(work_dir, flat, rows, tier=tier)
            print(f"[DELTAS] aggregate → {dp}", flush=True)
        if not getattr(args, "disable_evolution_state_snapshots", False):
            snap = append_evolution_state_snapshot(state_history_dir, state, state_snap_seq, reason)
            if snap is not None:
                logger.info("[STATE] snapshot -> %s", snap.name)

    start_it = max(1, int(args.start_iteration))
    num_it = int(args.num_iterations)
    if getattr(args, "resume", False):
        inferred = _infer_resume_start_iteration_from_stats(work_dir, tier, num_it)
        if inferred is not None and int(args.start_iteration) == 1:
            start_it = inferred
            logger.info("[RESUME] inferred --start-iteration=%s from work_dir artifacts", start_it)
        elif inferred is not None and start_it != inferred:
            logger.warning(
                "[RESUME] --start-iteration=%s differs from inferred %s; using explicit value.",
                start_it,
                inferred,
            )
    if start_it > num_it:
        logger.error("--start-iteration (%s) > --num-iterations (%s)", start_it, num_it)
        sys.exit(1)

    qualifying_keys_from_prev: Optional[Set[Tuple[str, str]]] = None
    pred_snapshot_prev_outer: Dict[Tuple[str, int], Dict[str, Any]] = {}
    refine_accumulator: Dict[str, Any] = {}

    if start_it > 1:
        prev_iter_path = work_dir / f"delta_method_predictions_iteration_{start_it - 1}.json"
        if prev_iter_path.is_file():
            try:
                prev_doc = json.loads(prev_iter_path.read_text(encoding="utf-8"))
                qualifying_keys_from_prev = _qualifying_keys_from_predictions_doc(
                    prev_doc, next_sweep_thr, args.feedback_on
                )
                pred_snapshot_prev_outer = build_prediction_snapshot_for_memory(
                    _user_pred_by_idx_from_predictions_doc(prev_doc)
                )
                logger.info(
                    "[RESUME] loaded %s qualifying posts from %s",
                    len(qualifying_keys_from_prev),
                    prev_iter_path.name,
                )
            except (OSError, json.JSONDecodeError) as e:
                logger.error("[RESUME] could not load %s: %s", prev_iter_path, e)
                sys.exit(1)
        else:
            logger.error(
                "[RESUME] missing %s (required when --start-iteration > 1). "
                "Run iteration %s first or use --start-iteration 1.",
                prev_iter_path,
                start_it - 1,
            )
            sys.exit(1)

    resume_seed_path = Path(
        str(getattr(args, "resume_user_pred_seed", "") or "").strip()
    ).expanduser()
    if getattr(args, "resume", False) and not str(getattr(args, "resume_user_pred_seed", "") or "").strip():
        resume_seed_path = work_dir / "delta_method_predictions.json"

    for iteration in range(start_it, num_it + 1):
        user_pred_by_idx: Dict[str, Dict[int, Dict[str, Any]]] = {}
        if getattr(args, "resume", False) and resume_seed_path.is_file():
            try:
                seed_doc = json.loads(resume_seed_path.read_text(encoding="utf-8"))
                user_pred_by_idx = _user_pred_by_idx_from_predictions_doc(seed_doc)
                logger.info(
                    "[RESUME] loaded user predictions from %s (%s review rows)",
                    resume_seed_path,
                    sum(len(v) for v in user_pred_by_idx.values()),
                )
            except (OSError, json.JSONDecodeError) as e:
                logger.error("[RESUME] could not load seed predictions %s: %s", resume_seed_path, e)
                sys.exit(1)
        all_delta_rows: List[Dict[str, Any]] = []
        if getattr(args, "resume", False):
            tier_label = f"tier{int(tier)}"
            deltas_path = work_dir / f"linkedin_{tier_label}_all_reviews_deltas.json"
            if deltas_path.is_file():
                try:
                    existing = json.loads(deltas_path.read_text(encoding="utf-8"))
                    prior_rows = existing.get("deltas") or []
                    if isinstance(prior_rows, list):
                        all_delta_rows.extend(copy.deepcopy(prior_rows))
                        logger.info("[RESUME] restored %s prior delta rows from %s", len(prior_rows), deltas_path.name)
                except (OSError, json.JSONDecodeError) as e:
                    logger.warning("[RESUME] could not load prior deltas %s: %s", deltas_path, e)
        iteration_jsds: List[float] = []
        resume_same_outer = bool(
            getattr(args, "resume", False)
            and int(state.get("last_completed_iteration") or 0) == int(iteration)
        )
        if resume_same_outer:
            processed = {str(x) for x in (state.get("processed_post_ids") or [])}
            logger.info(
                "[RESUME] continuing outer iteration %s | batch_seq=%s | processed_posts=%s",
                iteration,
                state.get("batch_seq"),
                len(processed),
            )
        else:
            processed = set()
            state["batch_seq"] = 0

        if qualifying_keys_from_prev is None:
            pending = list(all_posts)
        else:
            pending = [
                (pid, b, p)
                for pid, b, p in all_posts
                if (str(pid), str(p.get("post_id") or "")) in qualifying_keys_from_prev
            ]
        n_posts = len(pending)
        sweep_note = (
            "full sweep (all posts)"
            if qualifying_keys_from_prev is None
            else f"filtered | prior sweep post-correction ({args.feedback_on}) delta > {next_sweep_thr}"
        )
        print(
            f"\n{'=' * 72}\n[ITER {iteration}/{num_it}] {sweep_note} | posts={n_posts} | "
            f"group_words={word_count_t1(state.get('group_summary') or '')}\n{'=' * 72}\n",
            flush=True,
        )
        if qualifying_keys_from_prev is not None and n_posts == 0:
            logger.warning(
                "[ITER %s] no posts qualified from previous sweep (best_deltas %s <= %s for all); "
                "skipping batch work this iteration.",
                iteration,
                args.feedback_on,
                next_sweep_thr,
            )

        min_mem_posts = int(args.min_posts_for_memory_batch)
        # (linkedin memory review id, delta-bridge analysis, post category) — flush when >= min_mem_posts
        mem_buffer: List[Tuple[str, str, str]] = []
        mem_bs = int(args.memory_batch_size)

        if n_posts > 0:
            print(
                f"[ITER {iteration}] Phase baseline: {n_posts} posts — "
                f"{'hydrate i0 from ' + precomputed_i0_path.name if iteration == 1 else 'carry forward prior best (fallback to i0)'}",
                flush=True,
            )

        for i in range(0, n_posts, mem_bs):
            batch = pending[i : i + mem_bs]

            batch_review_idx_key: List[Tuple[int, str]] = []
            for pid, b, p in batch:
                _pk = (str(pid), str(p.get("post_id") or ""))
                batch_review_idx_key.append(
                    post_key_to_meta.get(_pk, (0, f"{pid}_review_0")),
                )

            async def _one_i0(
                pid: str,
                b: Dict[str, Any],
                p: Dict[str, Any],
                ridx: int,
                rkey: str,
            ) -> Optional[str]:
                topics = list(p.get("topics_ordered") or [])
                post_id = p.get("post_id")
                prev = user_pred_by_idx.get(pid, {}).get(ridx)
                if getattr(args, "resume", False) and _post_baseline_done_this_iteration(prev, iteration):
                    return str(post_id) if post_id is not None else None
                slim = precomputed_i0_index.get((str(pid), int(ridx)))
                if not isinstance(slim, dict):
                    logger.error("[I0] internal: missing row for %s review_%s", pid, ridx)
                    sys.exit(1)
                # Iteration 1 seeds from precomputed i0. Later iterations should start from the
                # previous iteration's best-known prediction/deltas (so i1 uses i0; i2 uses i1, etc.).
                if iteration > 1 and isinstance(prev, dict):
                    rec = prev
                else:
                    rec = _slim_row_to_pipeline_entry(
                        slim,
                        weight_text=weight_text,
                        weight_theme=weight_theme,
                    )
                id0 = rec.get("initial_prediction_deltas") or {}
                stim = (p.get("stimulus") or "").strip()
                category = str(p.get("category") or "")
                iter_lab = f"outer{iteration}_{'baseline_prev_best' if (iteration > 1 and isinstance(prev, dict)) else 'initial'}"
                async with delta_rows_lock:
                    append_all_reviews_delta(
                        all_delta_rows,
                        review_key=rkey,
                        user_id=str(pid),
                        review_idx=ridx,
                        post_id=str(post_id) if post_id is not None else "",
                        text_delta=float(id0.get("text_delta", 0.0)),
                        theme_delta=float(id0.get("theme_delta", 0.0)),
                        overall_delta=float(id0.get("overall_delta", 0.0)),
                        prediction=rec.get("prediction") or {},
                        actual=rec.get("actual") or {},
                        category=category,
                        product_description=stim,
                        iter_label=iter_lab,
                    )
                user_pred_by_idx.setdefault(pid, {})[ridx] = rec
                rec["feedback_loop_iteration"] = iteration
                id0 = rec.get("initial_prediction_deltas") or {}
                res_d = _resolved_deltas_for_gates(rec)
                gate = _gate_delta_from_deltas(res_d, args.feedback_on)
                bd = rec.get("best_deltas") or id0
                pred_best = (rec.get("best_prediction_data") or rec.get("initial_prediction_data") or {}).get(
                    "predicted_themes"
                ) or {}
                gt = {k: float(v) for k, v in (p.get("topic_probabilities") or {}).items()}
                line: Dict[str, Any] = {
                    "pipeline": pipeline_name,
                    "phase": "baseline",
                    "outer_iteration": iteration,
                    "post_id": post_id,
                    "profile_id": pid,
                    "review_key": rkey,
                    "category": p.get("category"),
                    "topics_ordered": topics,
                    "predicted_topic_probabilities": round_prob_dict(pred_best if isinstance(pred_best, dict) else {}),
                    "ground_truth_topic_probabilities": round_prob_dict(gt),
                    "initial_theme_delta": float(id0.get("theme_delta", 0.0)),
                    "initial_overall_delta": float(id0.get("overall_delta", 0.0)),
                    "best_theme_delta": float(bd.get("theme_delta", id0.get("theme_delta", 0.0))),
                    "best_overall_delta": float(bd.get("overall_delta", id0.get("overall_delta", 0.0))),
                    "n_corrections": 0,
                    "memory_gate": memory_jsd_thr,
                    "passed_memory_gate": False,
                }
                async with trace_lock:
                    with open(trace_path, "a", encoding="utf-8") as tf:
                        tf.write(json.dumps(line, ensure_ascii=False) + "\n")
                return str(post_id) if post_id is not None else None

            results_i0 = await asyncio.gather(
                *[
                    _one_i0(pid, b, p, batch_review_idx_key[j][0], batch_review_idx_key[j][1])
                    for j, (pid, b, p) in enumerate(batch)
                ]
            )

            for post_id in results_i0:
                if post_id:
                    processed.add(str(post_id))

            state["processed_post_ids"] = sorted(processed)
            state["last_completed_iteration"] = iteration
            persist_state(f"outer{iteration}_i0_offset{i}", user_pred_by_idx, all_delta_rows)

        pending_high: List[Tuple[str, Dict[str, Any], Dict[str, Any], int, str]] = []
        for pid, b, p in pending:
            _pk = (str(pid), str(p.get("post_id") or ""))
            ridx, rkey = post_key_to_meta.get(_pk, (0, f"{pid}_review_0"))
            row = user_pred_by_idx.get(pid, {}).get(ridx)
            if not isinstance(row, dict):
                continue
            if getattr(args, "resume", False) and _post_outer_iteration_complete(
                row,
                iteration,
                int(args.correction_iterations),
                delta_thr,
                args.feedback_on,
            ):
                continue
            # Use the *current baseline* deltas before starting this iteration's correction rounds:
            # i1 gates from i0; i2 gates from prior i1 best; etc.
            res_d = _resolved_deltas_for_gates(row)
            if _gate_delta_from_deltas(res_d, args.feedback_on) > delta_thr:
                pending_high.append((pid, b, p, ridx, rkey))

        n_high = len(pending_high)
        if n_high:
            print(
                f"[ITER {iteration}] Phase i1+: {n_high} posts with i0 "
                f"{args.feedback_on} delta > {delta_thr} — "
                "Part B memory batches → Part A corrections → refine cadence",
                flush=True,
            )
        elif n_posts:
            print(
                f"[ITER {iteration}] Phase i1+: no posts above --delta-threshold ({delta_thr})",
                flush=True,
            )

        async def _one_i1_part_a(
            pid: str,
            b: Dict[str, Any],
            p: Dict[str, Any],
            ridx: int,
            rkey: str,
        ) -> Dict[str, Any]:
            topics = list(p.get("topics_ordered") or [])
            post_id = p.get("post_id")
            group_text = (state.get("group_summary") or "").strip()
            qual = copy.deepcopy(state.get("qualitative_summary") or empty_qualitative_summary())
            cat_list = category_themes_for_post(str(p.get("category") or ""), theme_map)
            rec = user_pred_by_idx[pid][ridx]
            await run_correction_rounds_on_entry(
                rec,
                iteration_cap=args.correction_iterations,
                delta_threshold=delta_thr,
                weight_text=weight_text,
                weight_theme=weight_theme,
                feedback_on=args.feedback_on,
                client=client,
                sync_client=sync_client,
                gen_model=args.gen_model,
                delta_model=args.delta_model,
                topic_model=args.topic_model,
                sem=sem,
                profiles_root=profiles_root,
                group_summary=group_text,
                qualitative_summary=qual,
                profile_id=pid,
                review_idx=ridx,
                review_key=rkey,
                bundle=b,
                post=p,
                emb_cache=emb_cache,
                emb_lock=emb_lock,
                category_themes_list=cat_list,
                all_delta_rows=all_delta_rows,
                delta_rows_lock=delta_rows_lock,
                prob_threshold=args.prob_threshold,
                max_retries=args.max_retries,
                retry_delay=args.retry_delay,
                max_text_chars=args.max_text_chars,
                max_group_chars=args.max_group_chars,
                max_individual_chars=args.max_individual_chars,
                max_qual_json_chars=args.max_qual_json_chars,
                delta_rows_iter_prefix=f"outer{iteration}_",
                past_learnings=format_batch_analyses_memory_for_part_a(memory),
            )
            apply_select_best_prediction(rec)
            rec["feedback_loop_iteration"] = iteration
            id0 = rec.get("initial_prediction_deltas") or {}
            res_d = _resolved_deltas_for_gates(rec)
            gate = _gate_delta_from_deltas(res_d, args.feedback_on)
            card = load_profile_json(profiles_root, pid)
            delta_text: Optional[str] = None
            if gate > memory_jsd_thr:
                if getattr(args, "linkedin_root_cause_memory", True):
                    delta_text = "[queued_for_batch_root_cause_memory]"
                else:
                    delta_text = await _memory_analysis_for_delta_entry(
                        client,
                        args.delta_model,
                        args.max_qual_json_chars,
                        rec,
                        p,
                        group_text,
                        qual,
                        card,
                        b,
                    )
            bd = rec.get("best_deltas") or id0
            pred_best = (rec.get("best_prediction_data") or rec.get("initial_prediction_data") or {}).get(
                "predicted_themes"
            ) or {}
            gt = {k: float(v) for k, v in (p.get("topic_probabilities") or {}).items()}
            line: Dict[str, Any] = {
                "pipeline": pipeline_name,
                "phase": "i1",
                "outer_iteration": iteration,
                "post_id": post_id,
                "profile_id": pid,
                "review_key": rkey,
                "category": p.get("category"),
                "topics_ordered": topics,
                "predicted_topic_probabilities": round_prob_dict(pred_best if isinstance(pred_best, dict) else {}),
                "ground_truth_topic_probabilities": round_prob_dict(gt),
                "initial_theme_delta": float(id0.get("theme_delta", 0.0)),
                "initial_overall_delta": float(id0.get("overall_delta", 0.0)),
                "best_theme_delta": float(bd.get("theme_delta", id0.get("theme_delta", 0.0))),
                "best_overall_delta": float(bd.get("overall_delta", id0.get("overall_delta", 0.0))),
                "n_corrections": len(rec.get("correction_journey") or []),
                "memory_gate": memory_jsd_thr,
                "passed_memory_gate": bool(delta_text),
            }
            if delta_text:
                line["delta"] = delta_text
            async with trace_lock:
                with open(trace_path, "a", encoding="utf-8") as tf:
                    tf.write(json.dumps(line, ensure_ascii=False) + "\n")
            return {"post_id": post_id, "delta": delta_text}

        for i in range(0, n_high, mem_bs):
            batch_hi = pending_high[i : i + mem_bs]

            # Part B first: queue batch-memory candidates from the latest *pre-correction* deltas
            # (i0 for i1; prior best for later outer iterations), before Part A corrections.
            for (_pid, _b, p, ridx, _rkey) in batch_hi:
                rec_pre = user_pred_by_idx.get(_pid, {}).get(ridx)
                if not isinstance(rec_pre, dict):
                    continue
                pre_corr = _resolved_deltas_for_gates(rec_pre)
                if (
                    getattr(args, "linkedin_root_cause_memory", True)
                    and _gate_delta_from_deltas(pre_corr, args.feedback_on) > memory_jsd_thr
                ):
                    mem_buffer.append(
                        (
                            linkedin_review_key(_pid, p.get("post_id")),
                            "[queued_for_batch_root_cause_memory]",
                            str(p.get("category") or "").strip(),
                        )
                    )

            flushed_memory_this_chunk = False
            while len(mem_buffer) >= min_mem_posts:
                slice_rows = mem_buffer[:min_mem_posts]
                mem_buffer = mem_buffer[min_mem_posts:]
                batch_key = memory_batch_key(iteration, int(state["batch_seq"]))
                revs = [t[0] for t in slice_rows]
                ans = [t[1] for t in slice_rows]
                if (
                    getattr(args, "linkedin_root_cause_memory", True)
                    and len(slice_rows) == len(ans)
                ):
                    rc_items: List[Dict[str, Any]] = []
                    rc_ok = True
                    for rk, prelim, _cat in slice_rows:
                        pid_rc, post_id_rc = split_linkedin_memory_review_key(str(rk))
                        if not pid_rc or not post_id_rc:
                            rc_ok = False
                            break
                        tup = post_bundle_by_key.get((pid_rc, str(post_id_rc)))
                        if not tup:
                            rc_ok = False
                            break
                        _bundle_rc, post_rc = tup
                        ridx_rc, _rk2 = post_key_to_meta.get(
                            (pid_rc, str(post_id_rc)),
                            (0, f"{pid_rc}_review_0"),
                        )
                        rec_rc = user_pred_by_idx.get(pid_rc, {}).get(ridx_rc)
                        if not isinstance(rec_rc, dict):
                            rc_ok = False
                            break
                        pred_rc = resolve_failed_prediction_for_memory_batch(
                            profile_id=str(pid_rc),
                            review_idx=int(ridx_rc),
                            entry=rec_rc,
                            snapshot_before_current_iteration=pred_snapshot_prev_outer,
                        )
                        gt_rc = {
                            k: float(v) for k, v in (post_rc.get("topic_probabilities") or {}).items()
                        }
                        topics_rc = list(post_rc.get("topics_ordered") or [])
                        body_rc = (post_rc.get("body_text") or "").strip()
                        stim_rc = (post_rc.get("stimulus") or "").strip()
                        syn_rc = str(pred_rc.get("review_text") or "").strip()
                        if not syn_rc:
                            syn_rc = "(no synthetic post — topic distribution prediction only)"
                        rc_items.append(
                            {
                                "review_key": str(rk),
                                "category": str(post_rc.get("category") or ""),
                                "stimulus": stim_rc,
                                "actual_review_text": body_rc,
                                "failed_prediction_text": syn_rc,
                                "topics_ordered": topics_rc,
                                "predicted_themes": pred_rc.get("predicted_themes")
                                if isinstance(pred_rc.get("predicted_themes"), dict)
                                else {},
                                "ground_truth_topic_probabilities": gt_rc,
                                "preliminary_delta_notes": str(prelim),
                            }
                        )
                    if rc_ok and len(rc_items) == len(ans):
                        rc_model = (
                            str(getattr(args, "linkedin_root_cause_model", "") or "").strip()
                            or args.delta_model
                        )
                        ans = await linkedin_memory_batch_root_cause_analyses(
                            client,
                            rc_model,
                            state=state,
                            memory=memory,
                            pending_memory_keys=set(),
                            review_items=rc_items,
                            fallback_analyses=ans,
                        )
                cats_slice = sorted({t[2] for t in slice_rows if t[2]})
                if len(cats_slice) == 1:
                    category_field = cats_slice[0]
                elif cats_slice:
                    category_field = " | ".join(cats_slice)
                else:
                    category_field = ""
                refine_accumulator[batch_key] = {
                    "reviews": revs,
                    "analyses": ans,
                    "category": category_field,
                }
                memory[batch_key] = refine_accumulator[batch_key]
                save_memory(memory_path, memory)
                state["batch_seq"] = int(state["batch_seq"]) + 1
                persist_state(f"after_batch_{batch_key}", user_pred_by_idx, all_delta_rows)
                flushed_memory_this_chunk = True
                logger.info(
                    "[MEMORY] iter=%s flushed key=%s | high_delta_posts_in_batch=%s | buffer_remaining=%s",
                    iteration,
                    batch_key,
                    min_mem_posts,
                    len(mem_buffer),
                )

            results = await asyncio.gather(
                *[_one_i1_part_a(t[0], t[1], t[2], t[3], t[4]) for t in batch_hi],
            )

            for r in results:
                pid = r.get("post_id")
                if pid:
                    processed.add(str(pid))

            state["processed_post_ids"] = sorted(processed)
            state["last_completed_iteration"] = iteration

            if not flushed_memory_this_chunk:
                trace_key = f"iter{iteration}_i1_traceonly_offset{i}"
                persist_state(f"after_traceonly_{trace_key}", user_pred_by_idx, all_delta_rows)
                if len(batch_hi) < mem_bs:
                    logger.debug(
                        "[BATCH] iter=%s final partial i1 chunk | posts=%s (no memory flush this chunk)",
                        iteration,
                        len(batch_hi),
                    )

            is_last_batch = i + len(batch_hi) >= n_high
            min_refine_batches = int(args.evolve_every_n_batches)
            should_refine = len(refine_accumulator) >= min_refine_batches
            if not should_refine and is_last_batch and refine_accumulator:
                should_refine = True
            if should_refine:
                before = state.get("group_summary") or ""
                qual_before_refine = copy.deepcopy(
                    state.get("qualitative_summary") or empty_qualitative_summary()
                )
                q_baseline = state.get("baseline_qualitative_summary") or empty_qualitative_summary()
                window_logs = copy.deepcopy(refine_accumulator)
                refine_accumulator.clear()
                logger.info(
                    "[REFINE] starting persona refine | window_batches=%s (last memory batches) | "
                    "group_words_before=%s",
                    len(window_logs),
                    word_count_t1(before),
                )
                new_summary, new_qual_unified = await refine_group_summary(
                    client,
                    args.refine_model,
                    before,
                    baseline_group,
                    memory,
                    window_logs,
                    0,
                    qual_before_refine,
                    baseline_qualitative_summary=q_baseline,
                )
                if window_logs:
                    rk = sort_memory_batch_keys(list(window_logs.keys()))
                    if rk:
                        state["last_refined_memory_batch_key"] = rk[-1]
                if new_summary:
                    state["refinement_call_count"] = int(state.get("refinement_call_count") or 0) + 1
                    rc = int(state["refinement_call_count"])
                    max_wc = int(args.group_summary_max_words)
                    min_wc = int(args.shrink_floor_words)
                    baseline_min_wc = baseline_summary_min_words(
                        baseline_group,
                        floor_words=min_wc,
                        floor_ratio=float(args.shrink_baseline_floor_ratio),
                    )
                    integrated = normalize_group_summary_refinement(
                        baseline=baseline_group,
                        prior=before,
                        candidate=new_summary,
                    )
                    state["group_summary"] = guard_group_summary_text(
                        candidate=integrated,
                        prior=before,
                        baseline=baseline_group,
                        max_words=max_wc,
                        min_words=baseline_min_wc,
                    )
                    if word_count_t1(state["group_summary"]) > max_wc:
                        state["group_summary"] = hard_truncate_words(
                            state["group_summary"], max_wc
                        )
                        logger.info(
                            "[REFINE] capped group_summary to ≤%s words (hard truncate).",
                            max_wc,
                        )
                    logger.info(
                        "[REFINE] group_summary words %s → %s",
                        word_count_t1(before),
                        word_count_t1(state["group_summary"]),
                    )
                    persist_state(f"after_group_refine_{rc}", user_pred_by_idx, all_delta_rows)
                    if new_qual_unified is not None:
                        state["qualitative_summary"] = new_qual_unified
                        state["qual_refinement_call_count"] = int(
                            state.get("qual_refinement_call_count") or 0
                        ) + 1
                        qrc_u = int(state["qual_refinement_call_count"])
                        persist_state(f"after_qual_refine_{qrc_u}", user_pred_by_idx, all_delta_rows)
                    qual_for_shrink = copy.deepcopy(
                        state.get("qualitative_summary") or empty_qualitative_summary()
                    )
                    max_traits = int(args.max_traits_per_category)
                    traits_over = qualitative_summary_over_trait_limit(
                        qual_for_shrink,
                        max_traits_per_category=max_traits,
                    )
                    summary_words = word_count_t1(state["group_summary"])
                    summary_over = summary_words > max_wc
                    shrink_min_wc = max(min_wc, baseline_min_wc)

                    if summary_over and not traits_over:
                        prior_sum = state["group_summary"]
                        state["group_summary"] = hard_truncate_words(prior_sum, max_wc)
                        logger.info(
                            "[SHRINK] skipped LLM — summary %s words > %s but traits within "
                            "≤%s/category; hard-truncated to %s words.",
                            summary_words,
                            max_wc,
                            max_traits,
                            word_count_t1(state["group_summary"]),
                        )
                    elif traits_over:
                        qual_before_shrink = copy.deepcopy(qual_for_shrink)
                        shrunk_sum, shrunk_qual = await shrink_group_summary(
                            client,
                            args.shrink_model,
                            state["group_summary"],
                            baseline_group,
                            shrink_min_wc,
                            max_wc,
                            qual_for_shrink,
                        )
                        if shrunk_sum:
                            state["group_summary"] = guard_shrink_group_summary(
                                baseline=baseline_group,
                                prior=state["group_summary"],
                                candidate=shrunk_sum,
                                max_words=max_wc,
                                min_words=shrink_min_wc,
                            )
                        elif summary_over:
                            state["group_summary"] = hard_truncate_words(
                                state["group_summary"], max_wc
                            )
                            logger.warning(
                                "[SHRINK] LLM failed — hard-truncated summary to ≤%s words.", max_wc
                            )
                        if shrunk_qual is not None and not qualitative_summary_regression(
                            qual_before_shrink, shrunk_qual
                        ):
                            state["qualitative_summary"] = merge_qualitative_refinement(
                                baseline=q_baseline,
                                prior=qual_before_shrink,
                                candidate=shrunk_qual,
                                max_new_per_category=0,
                            )
                        elif shrunk_qual is not None:
                            logger.warning(
                                "[SHRINK] rejected qualitative_summary regression; keeping pre-shrink traits."
                            )
                        state["shrink_call_count"] = int(state.get("shrink_call_count") or 0) + 1
                        persist_state(
                            f"after_group_shrink_{state['shrink_call_count']}",
                            user_pred_by_idx,
                            all_delta_rows,
                        )
                    else:
                        logger.info(
                            "[SHRINK] skipped — within limits (≤%s words, ≤%s traits/category).",
                            max_wc,
                            max_traits,
                        )

                    if persona_evolver is not None:
                        analyses_blob = build_refine_analyses_text(
                            memory,
                            window_logs,
                            0,
                            window_only=True,
                        )
                        merged_for_qual = window_logs
                        q_base = state.get("baseline_qualitative_summary") or empty_qualitative_summary()
                        q_cur = copy.deepcopy(state.get("qualitative_summary") or empty_qualitative_summary())

                        if new_qual_unified is None:

                            def _qual_refine() -> Optional[Dict[str, Any]]:
                                return persona_evolver.run_single_refinement_step(
                                    merged_for_qual,
                                    q_cur,
                                    q_base,
                                    batch_analyses_text=analyses_blob,
                                    group_summary=(state.get("group_summary") or ""),
                                )

                            refined_q = await asyncio.to_thread(_qual_refine)
                            if refined_q:
                                state["qualitative_summary"] = merge_qualitative_refinement(
                                    baseline=q_base,
                                    prior=q_cur,
                                    candidate=refined_q,
                                    max_new_per_category=5,
                                )
                                state["qual_refinement_call_count"] = int(
                                    state.get("qual_refinement_call_count") or 0
                                ) + 1
                                qrc_fb = int(state["qual_refinement_call_count"])
                                persist_state(f"after_qual_refine_{qrc_fb}", user_pred_by_idx, all_delta_rows)
                            else:
                                logger.warning(
                                    "[QUAL_REFINE] sync persona refine failed; keeping previous qualitative_summary."
                                )

                        qrc = int(state.get("qual_refinement_call_count") or 0)
                        if qrc > 0 and qualitative_summary_over_trait_limit(
                            state.get("qualitative_summary") or empty_qualitative_summary(),
                            max_traits_per_category=int(args.max_traits_per_category),
                        ):

                            def _qual_shrink() -> Optional[Dict[str, Any]]:
                                return persona_evolver.shrink_step(
                                    state["qualitative_summary"],
                                    group_summary=(state.get("group_summary") or ""),
                                )

                            q_before_qual_shrink = copy.deepcopy(
                                state.get("qualitative_summary") or empty_qualitative_summary()
                            )
                            shrunk_q = await asyncio.to_thread(_qual_shrink)
                            if shrunk_q and not qualitative_summary_regression(
                                q_before_qual_shrink, shrunk_q
                            ):
                                state["qualitative_summary"] = merge_qualitative_refinement(
                                    baseline=q_base,
                                    prior=q_before_qual_shrink,
                                    candidate=shrunk_q,
                                    max_new_per_category=0,
                                )
                                state["qual_shrink_call_count"] = int(
                                    state.get("qual_shrink_call_count") or 0
                                ) + 1
                                persist_state(
                                    f"after_qual_shrink_{state['qual_shrink_call_count']}",
                                    user_pred_by_idx,
                                    all_delta_rows,
                                )
                            elif shrunk_q:
                                logger.warning(
                                    "[QUAL_SHRINK] rejected regression; keeping pre-shrink qualitative_summary."
                                )

        iteration_jsds.clear()
        for pid, _b, p in pending:
            _pk = (str(pid), str(p.get("post_id") or ""))
            ridx, _rk = post_key_to_meta.get(_pk, (0, f"{pid}_review_0"))
            row = user_pred_by_idx.get(pid, {}).get(ridx)
            if isinstance(row, dict):
                iteration_jsds.append(
                    _gate_delta_from_deltas(_resolved_deltas_for_gates(row), args.feedback_on)
                )

        thr = float(memory_jsd_thr)
        stats_record: Dict[str, Any] = {
            "iteration": iteration,
            "num_iterations_planned": num_it,
            "pipeline": pipeline_predictions_name,
            "tier": tier,
            "n_posts_in_sweep": n_posts,
            "n_posts_i1_eligible": n_high,
            "n_posts_scored": len(iteration_jsds),
            "mean_gate_delta": round(statistics.mean(iteration_jsds), 6) if iteration_jsds else None,
            "median_gate_delta": round(statistics.median(iteration_jsds), 6) if iteration_jsds else None,
            "stdev_gate_delta": round(statistics.stdev(iteration_jsds), 6) if len(iteration_jsds) > 1 else None,
            "min_gate_delta": round(min(iteration_jsds), 6) if iteration_jsds else None,
            "max_gate_delta": round(max(iteration_jsds), 6) if iteration_jsds else None,
            "n_gate_above_memory_threshold": sum(1 for x in iteration_jsds if x > thr),
            "memory_threshold": thr,
            "next_sweep_min_delta": next_sweep_thr,
            "iteration_stats_use_post_correction_deltas": True,
            "feedback_on": args.feedback_on,
            "refinement_call_count_end": state.get("refinement_call_count"),
            "shrink_call_count_end": state.get("shrink_call_count"),
            "qual_refinement_call_count_end": state.get("qual_refinement_call_count"),
            "qual_shrink_call_count_end": state.get("qual_shrink_call_count"),
        }
        iteration_stats_path.parent.mkdir(parents=True, exist_ok=True)
        with open(iteration_stats_path, "a", encoding="utf-8") as sf:
            sf.write(json.dumps(stats_record, ensure_ascii=False) + "\n")

        iter_out = work_dir / f"delta_method_predictions_iteration_{iteration}.json"
        try:
            d_src = (
                Path(args.delta_style_predictions_output).expanduser().resolve()
                if str(getattr(args, "delta_style_predictions_output", "") or "").strip()
                else work_dir / "delta_method_predictions.json"
            )
            if d_src.is_file():
                iter_out.write_text(d_src.read_text(encoding="utf-8"), encoding="utf-8")
                print(f"[ITER {iteration}] snapshot predictions → {iter_out}", flush=True)
        except OSError as e:
            logger.warning("[ITER] could not write per-iteration predictions copy: %s", e)

        print(f"[ITER {iteration}] COMPLETE | stats → {iteration_stats_path.name}", flush=True)

        next_qualifying: Set[Tuple[str, str]] = set()
        for pid_str, by_idx in user_pred_by_idx.items():
            for row in by_idx.values():
                if not isinstance(row, dict):
                    continue
                g = _gate_delta_from_deltas(_resolved_deltas_for_gates(row), args.feedback_on)
                if g > next_sweep_thr:
                    post_id = str(row.get("post_id") or "")
                    if post_id:
                        next_qualifying.add((str(pid_str), post_id))
        qualifying_keys_from_prev = next_qualifying
        pred_snapshot_prev_outer = build_prediction_snapshot_for_memory(user_pred_by_idx)

    print(
        f"\n[DONE] state={state_path} | memory={memory_path} | traces={trace_path.name} | "
        f"predictions={out_path}\n",
        flush=True,
    )


def _flatten_user_predictions_for_aggregate(
    by_user: Dict[str, Dict[int, Dict[str, Any]]],
) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for uid, by_idx in by_user.items():
        if not isinstance(by_idx, dict) or not by_idx:
            continue
        out[uid] = [by_idx[i] for i in sorted(by_idx.keys())]
    return out

def main() -> None:
    default_desc = _RAW_PROFILES / "description.json"
    default_profiles = _RAW_PROFILES / "profiles"

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--tier",
        type=int,
        default=1,
        choices=(1, 2),
        help="Select tier-1 or tier-2 defaults for contextual/mapping/work-dir/i0 when paths are omitted.",
    )
    p.add_argument(
        "--contextual-json",
        type=str,
        default=None,
        help="GT contextual JSON. Default: tier-1 stimulus JSON or tier-2 ground_truth_extraction_tier2.json.",
    )
    p.add_argument(
        "--precomputed-i0-json",
        type=str,
        default=None,
        dest="precomputed_i0_json",
        help="Slim i0 JSON from run_linkedin_i0_only.py / initial prediction. "
        "Default: outputs/linkedin_tier{N}_sgo/default_run/linkedin_i0_only.json (tier 1 also checks Predictions/).",
    )
    p.add_argument("--description-json", type=str, default=str(default_desc))
    p.add_argument("--mapping-json", type=str, default=None)
    p.add_argument("--profiles-root", type=str, default=str(default_profiles))
    p.add_argument(
        "--work-dir",
        type=str,
        default="",
        help="Run directory. Empty → scripts_sgo/outputs/linkedin_tier{N}_sgo/default_run. "
        "Writes evolution/, memory/, traces, predictions.",
    )
    p.add_argument("--output", type=str, default=None)
    p.add_argument(
        "--model-type-used",
        type=str,
        default=None,
        dest="model_type_used",
        help="Written as model_type_used in the output JSON (default: linkedin_tier{N}_delta_method).",
    )
    p.add_argument("--gen-model", type=str, default=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"))
    p.add_argument("--topic-model", type=str, default=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"))
    p.add_argument("--delta-model", type=str, default=os.environ.get("OPENAI_DELTA_MODEL", "gpt-4o-mini"))
    p.add_argument(
        "--linkedin-root-cause-model",
        type=str,
        default="",
        dest="linkedin_root_cause_model",
        help="Model for batch memory root-cause prompt (default: same as --delta-model)",
    )
    _rcm = p.add_mutually_exclusive_group()
    _rcm.add_argument(
        "--linkedin-root-cause-memory",
        dest="linkedin_root_cause_memory",
        action="store_true",
        help="Use LinkedIn batch root-cause prompt for memory analyses (default on)",
    )
    _rcm.add_argument(
        "--no-linkedin-root-cause-memory",
        dest="linkedin_root_cause_memory",
        action="store_false",
        help="Legacy: per-post delta_bridge / memory analysis in memory (disables batch root-cause prompt)",
    )
    _rcm.set_defaults(linkedin_root_cause_memory=True)
    p.add_argument("--concurrent", type=int, default=8)
    p.add_argument("--max-posts", type=int, default=0, help="0 = all posts with GT")
    p.add_argument(
        "--correction-iterations",
        type=int,
        default=6,
        help="Max correction rounds i1..iN (each gated by --delta-threshold / --feedback-on); i0 comes from --precomputed-i0-json",
    )
    p.add_argument(
        "--delta-threshold",
        type=float,
        default=None,
        help=f"Stopping threshold (default: settings.DELTA_THRESHOLD={getattr(settings, 'DELTA_THRESHOLD', 0.2)})",
    )
    p.add_argument(
        "--feedback-on",
        choices=("theme", "overall"),
        default="theme",
        help="Legacy SGO uses theme_delta > threshold; use 'overall' for overall_delta gating.",
    )
    p.add_argument(
        "--weight-text",
        type=float,
        default=None,
        help="Override settings.WEIGHT_TEXT_DELTA for overall delta",
    )
    p.add_argument(
        "--weight-theme",
        type=float,
        default=None,
        help="Override settings.WEIGHT_THEME_DELTA for overall delta",
    )
    p.add_argument(
        "--gt-prob-threshold",
        type=float,
        default=0.5,
        help="Themes at/above this probability count as 'actual' for recall metrics only; "
        "theme JSD in logprobs mode uses full topic_probabilities.",
    )
    p.add_argument("--prob-threshold", type=float, default=0.5)
    p.add_argument("--max-retries", type=int, default=3)
    p.add_argument("--retry-delay", type=float, default=1.0)
    p.add_argument("--max-text-chars", type=int, default=12000)
    p.add_argument("--max-group-chars", type=int, default=12000)
    p.add_argument("--max-individual-chars", type=int, default=6000)
    p.add_argument("--max-qual-json-chars", type=int, default=10000)
    p.add_argument("--num-iterations", type=int, default=5, help="Outer sweeps (default 5): predict → memory → refine → repeat")
    p.add_argument(
        "--start-iteration",
        type=int,
        default=1,
        help="First outer iteration index (inclusive). Use with --num-iterations to resume mid-run.",
    )
    p.add_argument(
        "--resume",
        action="store_true",
        help="Resume an interrupted run in --work-dir: reload evolution/memory, prior deltas, "
        "qualifying posts from iteration N-1, and skip posts already finished in the current sweep.",
    )
    p.add_argument(
        "--resume-user-pred-seed",
        type=str,
        default="",
        dest="resume_user_pred_seed",
        help="JSON with partial user_predictions (default: <work-dir>/delta_method_predictions.json when --resume).",
    )
    p.add_argument(
        "--memory-batch-size",
        type=int,
        default=5,
        dest="memory_batch_size",
        help="Posts processed together per batch step (same role as tier1_sgo --batch-size)",
    )
    p.add_argument(
        "--min-posts-for-memory-batch",
        type=int,
        default=5,
        dest="min_posts_for_memory_batch",
        help="Chunks smaller than this skip writing to memory/batch_analyses.json (predictions still run)",
    )
    p.add_argument(
        "--evolve-every-n-batches",
        type=int,
        default=5,
        dest="evolve_every_n_batches",
        help="After this many full memory batches, run group_summary + persona refine (tier1_sgo parity)",
    )
    p.add_argument(
        "--memory-jsd-threshold",
        type=float,
        default=None,
        dest="memory_jsd_threshold",
        help="Gate for delta analyses into memory using post-correction resolved delta (default: --next-sweep-min-delta)",
    )
    p.add_argument(
        "--next-sweep-min-delta",
        type=float,
        default=0.2,
        dest="next_sweep_min_delta",
        help="After i1..iN, outer sweep 2+ only re-scores posts whose prior sweep best_deltas "
        "(theme vs overall per --feedback-on) exceeded this. Default 0.2.",
    )
    p.add_argument(
        "--refine-model",
        type=str,
        default=os.environ.get("OPENAI_REFINE_MODEL", "gpt-4o"),
        help="Model for group_summary refine",
    )
    p.add_argument(
        "--refine-memory-batches",
        type=int,
        default=12,
        dest="refine_memory_batches",
        help="How many prior memory batch keys to include when refining (lower = smaller prompt)",
    )
    p.add_argument(
        "--shrink-model",
        type=str,
        default=os.environ.get("OPENAI_SHRINK_MODEL", "gpt-4o"),
        help="Model for group_summary/traits shrink when limits are exceeded",
    )
    p.add_argument(
        "--group-summary-max-words",
        type=int,
        default=DEFAULT_GROUP_SUMMARY_MAX_WORDS,
        dest="group_summary_max_words",
        help="Shrink group_summary when word count exceeds this (default: 300)",
    )
    p.add_argument(
        "--max-traits-per-category",
        type=int,
        default=DEFAULT_MAX_TRAITS_PER_CATEGORY,
        dest="max_traits_per_category",
        help="Shrink traits when any category exceeds this count (default: 20)",
    )
    p.add_argument(
        "--shrink-baseline-ratio",
        type=float,
        default=1.22,
        dest="shrink_baseline_ratio",
        help="(Legacy) used only if you still call compute_group_summary_shrink_band manually",
    )
    p.add_argument(
        "--shrink-baseline-floor-ratio",
        type=float,
        default=0.90,
        dest="shrink_baseline_floor_ratio",
        help="(Legacy) baseline-relative floor for shrink band helper",
    )
    p.add_argument(
        "--shrink-floor-words",
        type=int,
        default=200,
        dest="shrink_floor_words",
        help="Soft minimum word target passed into shrink prompt when shrink runs",
    )
    p.add_argument(
        "--shrink-hard-max-words",
        type=int,
        default=DEFAULT_GROUP_SUMMARY_MAX_WORDS,
        dest="shrink_hard_max_words",
        help="Alias cap for shrink prompt (default: same as --group-summary-max-words)",
    )
    p.add_argument(
        "--qual-schema-model",
        type=str,
        default=os.environ.get("OPENAI_QUAL_SCHEMA_MODEL", "gpt-4o"),
        dest="qual_schema_model",
        help="Model for qualitative_summary (traits) refine + shrink",
    )
    p.add_argument(
        "--qual-shrink-every-n-refinements",
        type=int,
        default=5,
        dest="qual_shrink_every_n_refinements",
        help="(Deprecated) qual shrink now runs only when a category exceeds --max-traits-per-category",
    )
    p.add_argument(
        "--disable-qual-schema",
        action="store_true",
        dest="disable_qual_schema",
        help="Do not evolve qualitative_summary (group_summary + memory pipeline still run)",
    )
    p.add_argument(
        "--disable-evolution-state-snapshots",
        action="store_true",
        dest="disable_evolution_state_snapshots",
        help="Skip evolution/state_history copies (still writes evolution/evolution_state.json)",
    )
    p.add_argument(
        "--delta-style-predictions-output",
        type=str,
        default="",
        dest="delta_style_predictions_output",
        help="Override path for delta_method_predictions.json (default: <work-dir>/delta_method_predictions.json)",
    )
    _sync_desc = p.add_mutually_exclusive_group()
    _sync_desc.add_argument(
        "--sync-description-json",
        dest="sync_description_json",
        action="store_true",
        help="Update <work-dir>/description.json when evolving summaries/traits (seed read-only). "
        "By default this runs only after group/persona refine or shrink (aligned with --evolve-every-n-batches), "
        "not after every memory batch; use --sync-description-json-every-state-save for the old behavior.",
    )
    _sync_desc.add_argument(
        "--no-sync-description-json",
        dest="sync_description_json",
        action="store_false",
        help="Do not modify description.json from this script.",
    )
    p.add_argument(
        "--sync-description-json-every-state-save",
        action="store_true",
        dest="sync_description_json_every_state_save",
        help="Refresh description.json on every persist_state (memory batches, i0 chunks, traces). "
        "Default off: sync only after refine/shrink steps.",
    )
    p.set_defaults(sync_description_json=True, sync_description_json_every_state_save=False)
    args = p.parse_args()

    tier = int(args.tier)
    defs = _default_paths(tier)
    if not str(args.contextual_json or "").strip():
        args.contextual_json = str(defs["contextual"])
    if not str(args.mapping_json or "").strip():
        args.mapping_json = str(defs["mapping"])
    if not str(args.work_dir or "").strip():
        args.work_dir = str(defs["work"])
    if not str(args.output or "").strip():
        args.output = str(defs["output"])
    if not str(args.precomputed_i0_json or "").strip():
        args.precomputed_i0_json = str(defs["precomputed_i0"])
    if not str(args.model_type_used or "").strip():
        args.model_type_used = str(defs["model_type"])
    args.tier = tier

    try:
        asyncio.run(run_all(args))
    except KeyboardInterrupt:
        logger.warning(
            "Interrupted (Ctrl+C). Last completed batch may have persisted state/memory/snapshots under --work-dir."
        )
        sys.exit(130)


if __name__ == "__main__":
    main()
