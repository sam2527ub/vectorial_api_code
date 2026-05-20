#!/usr/bin/env python3
"""
LinkedIn tier-1 SGO-style feedback loop (local only).

**Intended pipeline (each outer iteration, default ``--num-iterations`` = 5):**

1. **Predict + deltas:** sweep all posts using **author profile**, current **``group_summary``**,
   **``qualitative_summary``** (traits seeded from ``description.json`` ``traits`` →
   ``inherent_behavioral_traits`` with descriptions as the working “characteristics”), and
   **stimulus**. Score vs GT (JSD on full topic probabilities; optionally full synthetic-post pipeline
   via ``--full-delta-post-pipeline``).
2. **High-error → memory:** posts with **JSD > ``--jsd-threshold``** get a preliminary delta analysis; when a
   full qualifying batch is written to ``memory/batch_analyses.json``, a **batch root-cause** pass
   (``prompts/part_b_memory_analysis.txt``) rewrites ``analyses`` unless
   ``--no-linkedin-root-cause-memory``. Batches use **``--batch-size``** when **≥ ``--min-posts-for-feedback-batch``**
   posts (defaults: 5 and 5).
3. **Refine from memory:** after **``--evolve-every-n-batches``** full qualifying batches (default 5),
   run **group_summary** refine (+ mandatory shrink), then **qualitative_summary** (traits) refine /
   shrink on the same memory blob (same prompts as the micro-cluster SGO path). Baselines
   (**``baseline_group_summary``**, **``baseline_qualitative_summary``**) stay anchored to the initial
   seed from ``description.json``.
4. **Repeat:** next iteration uses the **updated** summaries + traits; a **working copy**
   ``<work_dir>/description.json`` is synced on each state save (the seed ``--description-json`` file
   is never modified) unless ``--no-sync-description-json``.

**Evolving context** = the long ``summary`` from ``description.json`` (**group_summary**)
plus a **qualitative_summary** persona schema (traits with rationalization and confidence).

On **first run** (new ``evolution_state.json``), ``qualitative_summary`` is seeded from
``description.json`` → ``traits`` (title / keywordTags / descriptions → inherent_behavioral_traits).
After each state save, by default **``<work_dir>/description.json``** is updated (copied once from
the seed ``--description-json`` on first run) with the current ``group_summary`` (``summary``) and
``traits`` rebuilt from the evolved ``qualitative_summary``. The seed file stays read-only. Use
``--no-sync-description-json`` to skip updating the working copy.

Per post (within step 1):
  - **Prediction inputs (no post-body leakage by default):** evolving **group summary**,
    **qualitative_summary** JSON, **author** card from ``profile.json``, and **stimulus** only.
    The main **post body is not** sent to the topic Yes/No calls (GT ``topic_probabilities``
    still came from the full post in a separate labeling step).
  - Predict topics (Yes/No + logprobs via ``utils.topic_presence_logprobs``).
  - JSD vs ground truth; only posts with **JSD > threshold** get a delta string → only those
    analyses enter batch memory for refine/shrink (low-JSD posts are not fed to the feedback loop).
  - **Feedback batches:** only chunks with **≥ ``--min-posts-for-feedback-batch``** posts (default 5) are
    written to ``memory/batch_analyses.json`` and enter the refine window; shorter tail chunks still get
    predictions + traces + JSD stats, but **no** memory/refine slot.
  - After **``--evolve-every-n-batches`` full** memory batches accumulate (default **5** batches of ≥5
    posts each), run one refine **group_summary** from rolling memory → mandatory **shrink**;
    then refine **qualitative_summary** (traits with ``text`` / ``rationalization`` / ``confidence_score``)
    via the same memory blob; **shrink** persona schema every ``--qual-shrink-every-n-refinements`` steps.
  - Optional ``--prediction-include-post-body`` restores the old behavior (include post text
    in the classifier — **not** recommended for strict SGO; use for debugging only).
  - After each successful **persona** refine, an extra pass refines **trait description bullets**
    (the ``descriptions`` arrays that map to ``description.json``). Disable with
    ``--disable-trait-descriptions-refine``.

**Iterations** (``--num-iterations``, default 5): repeat a full sweep over all
posts so each pass uses the latest group summary. Progress mirrors the micro-cluster
pipeline: **``print`` to stdout (flushed)** plus INFO logs for banners, per-post
lines, group refine/shrink, and ``[Persona]`` refinement/shrink steps.

**Resume / pick up:** the same ``--work-dir`` reloads ``evolution/evolution_state.json`` (or legacy
flat ``evolution_state.json``, migrated once) and ``memory/batch_analyses.json``, so group summary,
persona, and batch memory continue.
``post_traces.jsonl`` and ``iteration_stats.jsonl`` are **append-only** across restarts.

**Start later sweep:** ``--start-iteration N`` (default 1) runs only iterations ``N`` …
``--num-iterations`` inclusive. Example: finished 1–2, then
``--start-iteration 3 --num-iterations 5`` runs sweeps 3, 4, 5 using saved state.
Does **not** skip posts within a sweep if a process died mid-batch; it restarts the
current iteration’s batching from the beginning of that sweep.

  python scripts/scripts_sgo/linkedin/tier1_sgo_feedback_loop.py

Requires ``OPENAI_API_KEY``. Optional: ``OPENAI_BASE_URL``, repo-root ``.env``. Default
``--concurrent`` is **24** (override with ``OPENAI_TIER1_CONCURRENT``, ``OPENAI_CONCURRENT``, or the flag).

**Memory file** (``memory/batch_analyses.json``): same shape as micro-cluster SGO — top-level
keys like ``iteration_1_batch_2``; each value is ``{ "reviews": [...], "analyses": [...], "category": "..." }``.
``reviews`` entries are LinkedIn-style ids ``{profile_id}_post_{post_id}``, aligned with
``analyses`` (one row per high-JSD delta in that batch). Multiple categories in one batch are
joined with ``" | "``.

**State snapshots:** each time ``evolution_state.json`` is saved, a copy is also written under
``evolution/state_history/NNNNNN_<reason>.json`` (monotonic counter, survives resume). Reasons
include ``after_batch_*``, ``after_group_refine_*``, ``after_group_shrink_*``, ``after_qual_refine_*``,
``after_qual_shrink_*``. Use ``--disable-evolution-state-snapshots`` to skip copies.

**Per-post predictions** live in ``post_traces.jsonl`` (``predicted_topic_probabilities``,
``ground_truth_topic_probabilities``, ``topics_ordered``). **Per-iteration JSD stats** append to
``iteration_stats.jsonl`` (mean, median, stdev, min, max, count above threshold).

**Delta-style artifact (parity with ``tier1_delta_method_predictions.py``):** whenever
``evolution/evolution_state.json`` is saved, the loop also rewrites ``<work-dir>/delta_method_predictions.json``
with the same top-level keys (**``model_type_used``**, **``user_predictions``**, **``journey_log``**,
**``meta``**) so downstream journey tooling can consume one shape. By default ``correction_journey``
is empty (topic-only prediction per post). Pass **``--full-delta-post-pipeline``** to run the same
**synthetic post + embedding deltas + optional i1..iN corrections** as ``tier1_delta_method_predictions``
while keeping **memory/**, **evolution/** (state + snapshots), refines, and **description.json** seeding unchanged.
``meta`` includes feedback-loop counters. Use ``--no-delta-style-predictions-json`` to skip. Optional
``--save-all-reviews-deltas`` writes ``linkedin_tier1_all_reviews_deltas.json`` like the delta script.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
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

from generate_synthetic_review_and_memory_analysis.prompt_generation_for_both_parts import (
    format_batch_analyses_memory_for_part_a,
    format_linkedin_group_memory_posts_section,
    generate_part_b_linkedin_group_memory_prompt,
)


def _load_tier1_delta_predictions_module():
    """Load ``tier1_delta_method_predictions`` for correction helpers (``run_correction_rounds_on_entry``, etc.)."""
    path = Path(__file__).resolve().parent / "tier1_delta_method_predictions.py"
    spec = importlib.util.spec_from_file_location("tier1_delta_method_predictions", path)
    if not spec or not spec.loader:
        raise ImportError(f"Cannot load tier1 delta module from {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

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
    path = PROMPTS_DIR / "linkedin_group_summary_refine_prompt.txt"
    if not path.is_file():
        raise FileNotFoundError(f"Missing prompt: {path}")
    return path.read_text(encoding="utf-8")


def load_group_summary_shrink_template() -> str:
    # Use the unified shrink prompt (do not depend on linkedin_group_summary_shrink_prompt.txt).
    path = PROMPTS_DIR / "shrink_refinement.txt"
    if not path.is_file():
        raise FileNotFoundError(f"Missing prompt: {path}")
    return path.read_text(encoding="utf-8")


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
    #   { "text": str, "rationalization": str, "confidence_score": float }
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

    We intentionally emit **no keywordTags** (empty list). Each category becomes one block with
    ``descriptions`` containing the per-trait ``text`` strings.
    """
    labels = [
        ("skills_and_expertise", "Skills & Expertise"),
        ("working_style", "Working Style"),
        ("motivations_and_values", "Motivations & Values"),
        ("pain_points_and_needs", "Pain Points & Needs"),
        ("org_leadership_and_psychographic_profile", "Organizational Leadership & Psychographic Profile"),
    ]
    out: List[Dict[str, Any]] = []
    for key, title in labels:
        rows = q.get(key) if isinstance(q, dict) else None
        if not isinstance(rows, list) or not rows:
            continue
        descs: List[str] = []
        for tr in rows:
            text = _extract_trait_text(tr)
            if text:
                descs.append(text)
        if descs:
            out.append({"title": title, "keywordTags": [], "descriptions": descs})
    return out


def inherent_traits_as_linkedin_blocks(q: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Deprecated (old schema helper). Kept for compatibility; returns empty for the new schema."""
    return []


def load_linkedin_trait_descriptions_refine_template() -> str:
    path = PROMPTS_DIR / "linkedin_trait_descriptions_refine_prompt.txt"
    if not path.is_file():
        raise FileNotFoundError(f"Missing prompt: {path}")
    return path.read_text(encoding="utf-8")


def apply_refined_trait_descriptions(
    qualitative_summary: Dict[str, Any],
    llm_traits: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Merge model output ``traits`` into ``inherent_behavioral_traits`` (``text`` + ``linkedin_packaging``)."""
    if not isinstance(llm_traits, list) or not llm_traits:
        return None
    out = copy.deepcopy(qualitative_summary)
    inh = out.get("inherent_behavioral_traits")
    if not isinstance(inh, list) or not inh:
        return None
    by_title: Dict[str, Dict[str, Any]] = {}
    for lt in llm_traits:
        if isinstance(lt, dict) and str(lt.get("title") or "").strip():
            by_title[str(lt["title"]).strip().lower()] = lt
    changed = False
    for i, tr in enumerate(inh):
        if not isinstance(tr, dict):
            continue
        cur_rows = inherent_traits_as_linkedin_blocks({"inherent_behavioral_traits": [tr]})
        if not cur_rows:
            continue
        cur_title = cur_rows[0]["title"]
        key = cur_title.strip().lower()
        lt: Optional[Dict[str, Any]] = None
        if i < len(llm_traits) and isinstance(llm_traits[i], dict):
            lt = llm_traits[i]
            if str(lt.get("title", "")).strip().lower() != key:
                lt = by_title.get(key, lt)
        else:
            lt = by_title.get(key)
        if not lt or not isinstance(lt, dict):
            continue
        new_descs = lt.get("descriptions")
        if not isinstance(new_descs, list):
            continue
        seg = [str(x).strip() for x in new_descs if str(x).strip()]
        if not seg:
            continue
        title = str(lt.get("title") or cur_title).strip() or cur_title
        tags = list(cur_rows[0]["keywordTags"])
        if isinstance(lt.get("keywordTags"), list) and any(str(x).strip() for x in lt["keywordTags"]):
            tags = [str(x).strip() for x in lt["keywordTags"] if str(x).strip()]
        parts: List[str] = [title]
        if tags:
            parts.append(_KEYWORDS_LINE_PREFIX + " " + ", ".join(tags))
        parts.extend(seg)
        blob = "\n\n".join(parts)
        tr["text"] = blob
        tr["linkedin_packaging"] = {
            "title": title,
            "keywordTags": tags,
            "description_segments": seg,
        }
        changed = True
    return out if changed else None


def append_new_inherent_traits(
    qualitative_summary: Dict[str, Any],
    new_traits: List[Dict[str, Any]],
    *,
    max_new: int = 8,
) -> Optional[Dict[str, Any]]:
    """Append ``new_traits`` from the LLM as additional ``inherent_behavioral_traits`` rows."""
    if not isinstance(new_traits, list) or not new_traits:
        return None
    out = copy.deepcopy(qualitative_summary)
    inh = out.get("inherent_behavioral_traits")
    if not isinstance(inh, list):
        inh = []
        out["inherent_behavioral_traits"] = inh
    added = 0
    for nt in new_traits[: max(0, int(max_new))]:
        if not isinstance(nt, dict):
            continue
        title = str(nt.get("title") or "").strip()
        descs = nt.get("descriptions")
        if not title or not isinstance(descs, list):
            continue
        seg = [str(x).strip() for x in descs if str(x).strip()]
        if not seg:
            continue
        tags = [str(x).strip() for x in (nt.get("keywordTags") or []) if str(x).strip()]
        parts: List[str] = [title]
        if tags:
            parts.append(_KEYWORDS_LINE_PREFIX + " " + ", ".join(tags))
        parts.extend(seg)
        blob = "\n\n".join(parts)
        try:
            conf = float(nt.get("confidence_score", 0.72))
        except (TypeError, ValueError):
            conf = 0.72
        row: Dict[str, Any] = {
            "text": blob,
            "rationalization": "Added from memory-backed trait refine (delta pipeline).",
            "confidence_score": max(0.0, min(1.0, conf)),
            "linkedin_packaging": {
                "title": title,
                "keywordTags": tags,
                "description_segments": seg,
            },
        }
        inh.append(row)
        added += 1
    if added == 0:
        return None
    logger.info("[TRAITS] appended %s new inherent_behavioral_traits from model", added)
    return out


async def refine_linkedin_trait_descriptions(
    client: AsyncOpenAI,
    model: str,
    qualitative_summary: Dict[str, Any],
    batch_analyses_text: str,
) -> Optional[Dict[str, Any]]:
    """Deprecated for the new persona schema (5-category qualitative_summary).

    Trait texts are refined directly in ``qualitative_summary`` via the persona refine prompt, so
    the extra description-bullet rewrite pass is intentionally disabled.
    """
    return None


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
            "trait_descriptions_refine_disabled": bool(getattr(args, "disable_trait_descriptions_refine", False)),
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
    qj = json.dumps(qualitative_summary or {}, ensure_ascii=False, indent=2)
    if len(qj) > max_qual_json_chars:
        qj = qj[:max_qual_json_chars] + "\n... [truncated]"
    return (
        "=== Audience / tribe (evolving GROUP SUMMARY) ===\n"
        f"{gs}\n\n"
        "=== Persona schema (evolving qualitative_summary JSON — traits w/ rationalization & confidence) ===\n"
        f"{qj}\n\n"
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
        "Predictions used the evolving GROUP SUMMARY, qualitative persona schema (JSON traits), "
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
    max_chars_per_analysis: int = REFINE_ANALYSIS_LINE_MAX_CHARS,
    max_total_chars: int = REFINE_ANALYSES_BLOB_MAX_CHARS,
) -> str:
    """
    Refine sees (1) the current refinement window and (2) recent prior batches from disk memory
    so updates stay coherent and redundant themes can be merged across steps.
    """
    parts: List[str] = []
    parts.append("### CURRENT REFINEMENT WINDOW (primary — integrate these first)\n")
    parts.append(format_batch_logs_for_prompt(window_logs, max_chars_per_analysis=max_chars_per_analysis))
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
        json.dumps(q_state, ensure_ascii=False, indent=2),
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


def compute_shrink_target_words(
    current_text: str,
    baseline_text: str,
    *,
    floor_words: int,
    hard_max_words: int,
    baseline_ceiling_ratio: float,
) -> int:
    """Soft ceiling: stay near baseline size (ratio), never above hard_max, at least floor."""
    bw = max(_word_count(baseline_text), 1)
    cw = _word_count(current_text)
    cap = int(bw * baseline_ceiling_ratio)
    cap = min(max(cap, floor_words), hard_max_words)
    return min(cw, cap)


async def refine_group_summary(
    client: AsyncOpenAI,
    model: str,
    current_summary: str,
    baseline_summary: str,
    full_memory: Dict[str, Any],
    window_logs: Dict[str, Any],
    history_batches: int,
) -> Optional[str]:
    analyses_blob = build_refine_analyses_text(full_memory, window_logs, history_batches)
    template = load_group_summary_refine_template()
    max_gs = 28000
    full_prompt = template.format(
        current_group_summary=_truncate_for_refine_prompt(current_summary, max_gs, what="current_group_summary"),
        baseline_group_summary=_truncate_for_refine_prompt(baseline_summary, max_gs, what="baseline_group_summary"),
        batch_analyses_text=analyses_blob,
    )
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": full_prompt}],
            response_format={"type": "json_object"},
            temperature=0.25,
            max_tokens=4096,
            timeout=120.0,
        )
        raw = (resp.choices[0].message.content or "").strip()
        data = json.loads(raw)
        new_s = (data.get("summary") or "").strip()
        return new_s if new_s else None
    except Exception as e:
        logger.error("[REFINE] group_summary LLM failed: %s", e)
        return None


async def shrink_group_summary(
    client: AsyncOpenAI,
    model: str,
    text: str,
    baseline: str,
    target_max_words: int,
) -> Optional[str]:
    template = load_group_summary_shrink_template()
    cw = _word_count(text)
    prompt = template.format(
        current_group_summary=text,
        baseline_group_summary=baseline,
        target_max_words=int(target_max_words),
        current_word_count=int(cw),
        # Unified shrink template also supports persona shrink; provide safe default.
        current_summary_json="{}",
    )
    for attempt in range(2):
        try:
            resp = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.15 if attempt == 0 else 0.05,
                max_tokens=4096,
                timeout=120.0,
            )
            raw = (resp.choices[0].message.content or "").strip()
            data = json.loads(raw)
            new_s = (data.get("summary") or "").strip()
            if not new_s:
                continue
            nw = _word_count(new_s)
            if nw > target_max_words + 15:
                logger.warning(
                    "[SHRINK] model returned %s words > ceiling %s; hard-truncating.",
                    nw,
                    target_max_words,
                )
                new_s = hard_truncate_words(new_s, target_max_words)
            return new_s.strip()
        except Exception as e:
            logger.error("[SHRINK] attempt %s failed: %s", attempt + 1, e)
    return None


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

    if args.batch_size < args.min_posts_for_feedback_batch:
        _warn(
            "[CONFIG] batch_size=%s < min_posts_for_feedback_batch=%s — no chunk will qualify for "
            "memory/refine (increase --batch-size or lower --min-posts-for-feedback-batch).",
            args.batch_size,
            args.min_posts_for_feedback_batch,
        )

    if int(args.start_iteration) < 1:
        logger.error("[FATAL] --start-iteration must be >= 1 (got %s).", args.start_iteration)
        sys.exit(1)
    if int(args.start_iteration) > int(args.num_iterations):
        logger.error(
            "[FATAL] --start-iteration (%s) > --num-iterations (%s); nothing would run.",
            args.start_iteration,
            args.num_iterations,
        )
        sys.exit(1)

    contextual_path = Path(args.contextual_json).expanduser().resolve()
    work_dir = Path(args.work_dir).expanduser().resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    _map_arg = str(getattr(args, "mapping_json", "") or "").strip()
    mapping_path: Optional[Path] = Path(_map_arg).expanduser().resolve() if _map_arg else None
    state_path, state_history_dir = resolve_workdir_evolution_paths(work_dir)
    memory_path = work_dir / "memory" / "batch_analyses.json"
    trace_path = work_dir / "post_traces.jsonl"
    iteration_stats_path = work_dir / "iteration_stats.jsonl"
    state_snap_seq: Dict[str, int] = {"n": next_state_snapshot_seq(state_history_dir)}

    description_seed_path = Path(args.description_json).expanduser().resolve()
    description_work_path = ensure_workdir_description_from_seed(work_dir, description_seed_path)

    user_pred_map: Dict[str, Dict[int, Dict[str, Any]]] = {}
    all_feedback_delta_rows: List[Dict[str, Any]] = []

    def _persist_state(reason: str) -> None:
        save_state(state_path, state)
        if getattr(args, "sync_description_json", True):
            try:
                wd = ensure_workdir_description_from_seed(work_dir, description_seed_path)
                sync_description_json_from_state(wd, state)
            except OSError as e:
                logger.error("[DESCRIPTION] sync failed: %s", e)
        if getattr(args, "write_delta_style_predictions_json", True):
            try:
                d_out = (
                    Path(args.delta_style_predictions_output).expanduser().resolve()
                    if str(getattr(args, "delta_style_predictions_output", "") or "").strip()
                    else work_dir / "delta_method_predictions.json"
                )
                write_delta_method_predictions_artifact(
                    out_path=d_out,
                    user_pred_by_idx=user_pred_map,
                    all_delta_rows=all_feedback_delta_rows,
                    contextual_path=contextual_path,
                    description_work_path=description_work_path,
                    description_seed_path=description_seed_path,
                    mapping_path=mapping_path,
                    work_dir=work_dir,
                    state=state,
                    args=args,
                )
                if getattr(args, "save_all_reviews_deltas", False) and all_feedback_delta_rows:
                    dp = _write_feedback_aggregate_deltas(
                        work_dir,
                        _flatten_user_predictions_map(user_pred_map),
                        all_feedback_delta_rows,
                    )
                    logger.info("[DELTA_STYLE] aggregate deltas -> %s", dp)
            except Exception as e:
                logger.exception("[DELTA_STYLE] artifact write failed: %s", e)
        if args.disable_evolution_state_snapshots:
            return
        snap = append_evolution_state_snapshot(state_history_dir, state, state_snap_seq, reason)
        if snap is not None:
            logger.info("[STATE] snapshot -> %s", snap.name)

    seed_group_summary, desc_room = load_tribe_description(description_seed_path)
    initial_override = Path(args.initial_group_summary).expanduser() if args.initial_group_summary else None
    state = load_or_init_state(state_path, seed_group_summary, initial_override, description_seed_path)
    memory = load_memory(memory_path)

    profiles_root = Path(args.profiles_root).expanduser().resolve()
    profile_cache: Dict[str, Dict[str, Any]] = {}

    payload = load_contextual_payload(contextual_path)
    ctx_room = (payload.get("room_id") or "").strip()
    if desc_room and ctx_room and desc_room != ctx_room:
        logger.warning(
            "[DATA] audience_room_id mismatch: description.json=%r contextual=%r",
            desc_room,
            ctx_room,
        )

    all_posts = iter_posts_tier1(payload)
    _ensure_scoring_mode_logprobs()
    post_key_to_meta: Dict[Tuple[str, str], Tuple[int, str]] = {}
    _review_seq: Dict[str, int] = {}
    for _pid, _bundle, _post in all_posts:
        _pid_s = str(_pid)
        _post_id = str(_post.get("post_id") or "")
        if not _post_id:
            continue
        _kk = (_pid_s, _post_id)
        if _kk not in post_key_to_meta:
            _idx = _review_seq.get(_pid_s, 0)
            post_key_to_meta[_kk] = (_idx, f"{_pid_s}_review_{_idx}")
            _review_seq[_pid_s] = _idx + 1
    _echo(
        "[DATA] contextual=%s | posts_with_GT=%s | description_seed=%s",
        contextual_path.name,
        len(all_posts),
        description_seed_path.name,
    )
    _echo("Processing linkedin / tier1")
    _echo("  work_dir=%s", str(work_dir))
    _echo("  description_work=%s (seed %s is read-only)", description_work_path.name, description_seed_path.name)
    _echo(
        "  sync_description_json=%s (summary + traits → work_dir copy only)",
        getattr(args, "sync_description_json", True),
    )
    _echo(
        "  iteration_range=%s..%s (inclusive)",
        max(1, int(args.start_iteration)),
        int(args.num_iterations),
    )
    _echo(
        "  state=%s | memory=%s | traces=%s | iter_stats=%s",
        state_path.name,
        memory_path.name,
        trace_path.name,
        iteration_stats_path.name,
    )
    if not args.disable_evolution_state_snapshots:
        _echo("  evolution_state snapshots -> %s/ (on every state save)", state_history_dir.name)
    _echo(
        "  [PRED] topic prompts include post body=%s (default False = context+stimulus only, no leakage)",
        args.prediction_include_post_body,
    )
    if getattr(args, "write_delta_style_predictions_json", True):
        _dout = (
            Path(args.delta_style_predictions_output).expanduser().resolve()
            if str(getattr(args, "delta_style_predictions_output", "") or "").strip()
            else work_dir / "delta_method_predictions.json"
        )
        _echo("  delta_style_predictions_json -> %s", _dout.name)

    base_url = os.environ.get("OPENAI_BASE_URL")
    client = AsyncOpenAI(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=base_url if base_url and base_url.strip() else None,
        timeout=120.0,
    )

    sync_client = None
    emb_cache: Dict[str, Any] = {}
    emb_lock = Lock()
    _delta_mod: Any = None
    _theme_map_for_delta: Dict[str, List[str]] = {}
    correction_delta_thr = float(settings.DELTA_THRESHOLD)
    if getattr(args, "correction_delta_threshold", None) is not None:
        correction_delta_thr = float(args.correction_delta_threshold)
    if getattr(args, "full_delta_post_pipeline", False):
        from openai import OpenAI

        _echo(
            "  [DELTA] --full-delta-post-pipeline: i0 via run_audience_room_i0_for_post + "
            "tier1_delta_method_predictions.run_correction_rounds_on_entry"
        )
        sync_client = OpenAI(
            api_key=os.environ["OPENAI_API_KEY"],
            base_url=base_url if base_url and base_url.strip() else None,
            timeout=120.0,
        )
        _delta_mod = _load_tier1_delta_predictions_module()
        if mapping_path is not None and mapping_path.is_file():
            _theme_map_for_delta = _delta_mod.load_category_themes_map(mapping_path)

    sem = asyncio.Semaphore(max(1, args.concurrent))
    load_group_summary_refine_template()
    load_group_summary_shrink_template()
    # Deprecated for the new 5-category schema: extra pass that rewrote description.json bullet text.
    # Traits are refined directly in the qualitative_summary.

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
    if not args.disable_qual_schema:
        persona_evolver = TribeSchemaEvolver(
            cluster_id="linkedin",
            micro_id="tier1",
            memory_dir=str(work_dir / "memory"),
            refined_schema_dir=str(work_dir),
            evolution_model=args.qual_schema_model,
            refine_prompt_basename="refine_characteristics",
            shrink_prompt_basename="shrink_refinement",
        )
        _q0 = state.get("qualitative_summary") or {}
        _n_traits = sum(
            len((_q0.get(k) or []))
            for k in (
                "skills_and_expertise",
                "working_style",
                "motivations_and_values",
                "pain_points_and_needs",
                "org_leadership_and_psychographic_profile",
            )
            if isinstance(_q0.get(k), list) or _q0.get(k) is None
        )
        _echo(
            "  [Persona] Evolution ENABLED | model=%s | shrink every %s qualitative refinements | "
            "resume traits=%s",
            args.qual_schema_model,
            args.qual_shrink_every_n_refinements,
            _n_traits,
        )
    else:
        _echo("  [Persona] Evolution DISABLED (--disable-qual-schema).")

    trace_lock = asyncio.Lock()

    def _profile_card_for(pid: str) -> Dict[str, Any]:
        if pid not in profile_cache:
            profile_cache[pid] = load_profile_json(profiles_root, pid)
            if not profile_cache[pid]:
                logger.warning("[PROFILE] Missing profile.json: %s / %s", profiles_root, pid)
        return profile_cache[pid]

    async def process_one(
        iteration: int,
        pid: str,
        bundle: Dict[str, Any],
        post: Dict[str, Any],
        review_idx: int,
        review_key: str,
    ) -> Dict[str, Any]:
        topics = list(post.get("topics_ordered") or [])
        post_id = post.get("post_id")
        if not topics:
            _echo("[POST] iter=%s post_id=%s SKIP (no topics_ordered)", iteration, post_id)
            return {"post_id": None, "line": None, "delta": None}

        group_text = (state.get("group_summary") or "").strip()
        qual = copy.deepcopy(state.get("qualitative_summary") or empty_qualitative_summary())
        gt = {k: float(v) for k, v in (post.get("topic_probabilities") or {}).items()}
        card = _profile_card_for(pid)
        individual_ctx = format_individual_author_context(card, bundle)
        stim = (post.get("stimulus") or "").strip()
        body = (post.get("body_text") or "").strip()
        qual_excerpt = json.dumps(qual, ensure_ascii=False)[:5000]

        if getattr(args, "full_delta_post_pipeline", False) and sync_client is not None and _delta_mod is not None:
            cat_list = _delta_mod.category_themes_for_post(
                str(post.get("category") or ""), _theme_map_for_delta
            )
            wt_fd = float(getattr(settings, "WEIGHT_TEXT_DELTA", 0.7))
            wm_fd = float(getattr(settings, "WEIGHT_THEME_DELTA", 0.3))
            topic_m = str(getattr(args, "topic_model", "gpt-4o-mini"))
            ci = int(getattr(args, "correction_iterations", 5))
            gt_pt = float(getattr(args, "gt_prob_threshold", 0.5))
            fb_on = str(getattr(args, "correction_feedback_on", "theme"))
            entry = await run_audience_room_i0_for_post(
                review_key=review_key,
                profile_id=str(pid),
                review_idx=review_idx,
                post=post,
                client=client,
                sync_client=sync_client,
                gen_model=args.model,
                topic_model=topic_m,
                sem=sem,
                profiles_root=profiles_root,
                group_summary=group_text,
                qualitative_summary=qual,
                emb_cache=emb_cache,
                emb_lock=emb_lock,
                category_themes_list=cat_list,
                all_delta_rows=all_feedback_delta_rows,
                delta_rows_lock=None,
                prob_threshold=args.prob_threshold,
                max_retries=args.max_retries,
                retry_delay=args.retry_delay,
                max_text_chars=args.max_text_chars,
                delta_rows_iter_prefix="tier1_sgo_",
                description_json_path=description_work_path,
                debug_i0_prompt=False,
                gt_prob_threshold=gt_pt,
                weight_text=wt_fd,
                weight_theme=wm_fd,
                print_i0_prompt=False,
                prompt_print_lock=None,
            )
            await _delta_mod.run_correction_rounds_on_entry(
                entry,
                iteration_cap=ci,
                delta_threshold=correction_delta_thr,
                weight_text=wt_fd,
                weight_theme=wm_fd,
                feedback_on=fb_on,
                client=client,
                sync_client=sync_client,
                gen_model=args.model,
                delta_model=args.delta_model,
                topic_model=topic_m,
                sem=sem,
                profiles_root=profiles_root,
                group_summary=group_text,
                qualitative_summary=qual,
                profile_id=str(pid),
                review_idx=review_idx,
                review_key=review_key,
                bundle=bundle,
                post=post,
                emb_cache=emb_cache,
                emb_lock=emb_lock,
                category_themes_list=cat_list,
                all_delta_rows=all_feedback_delta_rows,
                delta_rows_lock=None,
                prob_threshold=args.prob_threshold,
                max_retries=args.max_retries,
                retry_delay=args.retry_delay,
                max_text_chars=args.max_text_chars,
                max_group_chars=args.max_group_chars,
                max_individual_chars=args.max_individual_chars,
                max_qual_json_chars=args.max_qual_json_chars,
                delta_rows_iter_prefix="tier1_sgo_",
                past_learnings=format_batch_analyses_memory_for_part_a(memory),
            )
            _delta_mod.apply_select_best_prediction(entry)
            entry["feedback_loop_iteration"] = iteration
            pred_init_raw = (entry.get("initial_prediction_data") or {}).get("predicted_themes") or {}
            pred_init: Dict[str, float] = {}
            if isinstance(pred_init_raw, dict):
                for k, v in pred_init_raw.items():
                    try:
                        pred_init[str(k)] = float(v)
                    except (TypeError, ValueError):
                        continue
            jsd = float(calculate_jsd(pred_init, gt, topics))
            line = {
                "iteration": iteration,
                "post_id": post_id,
                "profile_id": pid,
                "jsd": round(jsd, 6),
                "category": post.get("category"),
                "prediction_uses_post_body": bool(args.prediction_include_post_body),
                "topics_ordered": topics,
                "predicted_topic_probabilities": round_prob_dict(pred_init),
                "ground_truth_topic_probabilities": round_prob_dict(gt),
            }
            journey = entry.get("correction_journey") or []
            delta_text: Optional[str] = None
            if getattr(args, "linkedin_root_cause_memory", True):
                if jsd > float(args.jsd_threshold):
                    delta_text = "[queued_for_batch_root_cause_memory]"
            else:
                if journey:
                    parts = [
                        str(s.get("analysis_used") or "").strip()
                        for s in journey
                        if isinstance(s, dict) and s.get("analysis_used")
                    ]
                    delta_text = "\n\n---\n\n".join([p for p in parts if p]) or None
                if not delta_text and jsd > float(args.jsd_threshold):
                    delta_text = await delta_bridge_analysis(
                        client,
                        args.delta_model,
                        body,
                        stim,
                        str(post.get("category") or ""),
                        group_text,
                        qual_excerpt,
                        individual_ctx,
                        topics,
                        gt,
                        pred_init,
                        jsd,
                    )
            if delta_text:
                line["delta"] = delta_text
            if journey:
                _echo(
                    "[POST] iter=%s post_id=%s cat=%r full-delta | initial_JSD=%.4f corrections=%s",
                    iteration,
                    post_id,
                    post.get("category"),
                    jsd,
                    len(journey),
                )
            elif jsd > args.jsd_threshold:
                _echo(
                    "[POST] iter=%s post_id=%s cat=%r JSD=%.4f **DELTA** (threshold=%.4f) [full-delta]",
                    iteration,
                    post_id,
                    post.get("category"),
                    jsd,
                    args.jsd_threshold,
                )
            else:
                _echo(
                    "[POST] iter=%s post_id=%s cat=%r JSD=%.4f ok (threshold=%.4f) [full-delta]",
                    iteration,
                    post_id,
                    post.get("category"),
                    jsd,
                    args.jsd_threshold,
                )
            user_pred_map.setdefault(pid, {})
            user_pred_map[pid][review_idx] = entry
            bd = entry.get("best_deltas") or {}
            id0 = entry.get("initial_prediction_deltas") or {}
            text_d = float(id0.get("text_delta", 0.0))
            theme_d = float(bd.get("theme_delta", id0.get("theme_delta", 0.0)))
            overall_d = float(bd.get("overall_delta", id0.get("overall_delta", 0.0)))
            pred_for_row = copy.deepcopy(
                entry.get("prediction") or entry.get("initial_prediction_data") or {}
            )
            act_for_row = entry.get("actual") or {}
            _append_feedback_delta_row(
                all_feedback_delta_rows,
                review_key=review_key,
                user_id=pid,
                review_idx=review_idx,
                post_id=str(post_id),
                iteration_label=f"feedback_iter_{iteration}",
                text_delta=text_d,
                theme_delta=theme_d,
                overall_delta=overall_d,
                prediction=pred_for_row,
                actual=act_for_row,
                category=str(post.get("category") or ""),
                product_description=stim,
            )
            async with trace_lock:
                with open(trace_path, "a", encoding="utf-8") as tf:
                    tf.write(json.dumps(line, ensure_ascii=False) + "\n")
            return {"post_id": post_id, "line": line, "delta": delta_text}

        out = await process_single_post(
            client=client,
            model=args.model,
            post=post,
            topics=topics,
            semaphore=sem,
            prob_threshold=args.prob_threshold,
            max_retries=args.max_retries,
            retry_delay=args.retry_delay,
            max_text_chars=args.max_text_chars,
            include_post_body=args.prediction_include_post_body,
        )
        pred = out.get("topic_probabilities") or {}
        jsd = calculate_jsd(pred, gt, topics)
        line: Dict[str, Any] = {
            "iteration": iteration,
            "post_id": post_id,
            "profile_id": pid,
            "jsd": round(jsd, 6),
            "category": post.get("category"),
            "prediction_uses_post_body": bool(args.prediction_include_post_body),
            "topics_ordered": topics,
            "predicted_topic_probabilities": round_prob_dict(pred),
            "ground_truth_topic_probabilities": round_prob_dict(gt),
        }
        tlp = out.get("topic_logprobs")
        if isinstance(tlp, dict) and tlp:
            line["topic_logprobs"] = tlp
        delta_text: Optional[str] = None
        if jsd > args.jsd_threshold:
            if getattr(args, "linkedin_root_cause_memory", True):
                delta_text = "[queued_for_batch_root_cause_memory]"
            else:
                delta_text = await delta_bridge_analysis(
                    client,
                    args.delta_model,
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
            line["delta"] = delta_text
            _echo(
                "[POST] iter=%s post_id=%s cat=%r JSD=%.4f **DELTA** (threshold=%.4f)",
                iteration,
                post_id,
                post.get("category"),
                jsd,
                args.jsd_threshold,
            )
        else:
            _echo(
                "[POST] iter=%s post_id=%s cat=%r JSD=%.4f ok (threshold=%.4f)",
                iteration,
                post_id,
                post.get("category"),
                jsd,
                args.jsd_threshold,
            )

        wt = float(getattr(settings, "WEIGHT_TEXT_DELTA", 0.7))
        wm = float(getattr(settings, "WEIGHT_THEME_DELTA", 0.3))
        td0, thm0 = 0.0, float(jsd)
        ov0 = float(wt * td0 + wm * thm0)
        pred_probs = {k: float(v) for k, v in pred.items()}
        pred_block: Dict[str, Any] = {
            "review_text": "",
            "rating": None,
            "sentiment": None,
            "predicted_themes": pred_probs,
        }
        actual_block: Dict[str, Any] = {
            "review_text": body,
            "rating": None,
            "sentiment": None,
            "predicted_themes": gt,
            "topic_probabilities": gt,
        }
        metrics = calculate_review_metrics(
            pred_block, actual_block, all_themes=topics
        ) or {}
        initial_prediction_deltas = {"text_delta": td0, "theme_delta": thm0, "overall_delta": ov0}
        scoring = getattr(settings, "SCORING_MODE", "confidence")
        deltas_block = {
            "text_delta": td0,
            "theme_delta": thm0,
            "overall_delta": ov0,
            "theme_jsd": thm0 if scoring in ("logprobs", "logprobs-without-persona-context") else None,
            "delta_weights": {"text": wt, "theme": wm},
        }
        store_entry: Dict[str, Any] = {
            "review_key": review_key,
            "user_id": pid,
            "review_idx": review_idx,
            "status": "success",
            "product_description": stim,
            "category": str(post.get("category") or ""),
            "post_id": str(post_id),
            "prediction": copy.deepcopy(pred_block),
            "initial_prediction_data": copy.deepcopy(pred_block),
            "actual": actual_block,
            "metrics": metrics,
            "deltas": deltas_block,
            "initial_prediction_deltas": copy.deepcopy(initial_prediction_deltas),
            "initial_deltas": copy.deepcopy(initial_prediction_deltas),
            "i0_snapshot": {
                "prediction": copy.deepcopy(pred_block),
                "deltas": copy.deepcopy(initial_prediction_deltas),
                "metrics": copy.deepcopy(metrics),
            },
            "correction_journey": [],
            "feedback_loop_iteration": iteration,
        }
        user_pred_map.setdefault(pid, {})
        user_pred_map[pid][review_idx] = store_entry
        _append_feedback_delta_row(
            all_feedback_delta_rows,
            review_key=review_key,
            user_id=pid,
            review_idx=review_idx,
            post_id=str(post_id),
            iteration_label=f"feedback_iter_{iteration}",
            text_delta=td0,
            theme_delta=thm0,
            overall_delta=ov0,
            prediction=pred_block,
            actual=actual_block,
            category=str(post.get("category") or ""),
            product_description=stim,
        )

        async with trace_lock:
            with open(trace_path, "a", encoding="utf-8") as tf:
                tf.write(json.dumps(line, ensure_ascii=False) + "\n")
        return {"post_id": post_id, "line": line, "delta": delta_text}

    baseline_group = (state.get("baseline_group_summary") or seed_group_summary).strip()
    normalize_qualitative_in_state(state)

    start_it = max(1, int(args.start_iteration))
    for iteration in range(start_it, args.num_iterations + 1):
        _q = state.get("qualitative_summary") or {}
        _log_banner(
            f"ITERATION {iteration} / {args.num_iterations}  |  "
            f"group_summary={_word_count(state.get('group_summary', ''))} words  |  "
            "persona traits=%s (5-category schema)",
            sum(
                len((_q.get(k) or []))
                for k in (
                    "skills_and_expertise",
                    "working_style",
                    "motivations_and_values",
                    "pain_points_and_needs",
                    "org_leadership_and_psychographic_profile",
                )
                if isinstance(_q.get(k), list)
            ),
        )
        _echo(
            "[PIPELINE] (1) predict all posts + deltas | (2) JSD>%s → batch memory (size≥%s in batches of %s) | "
            "(3) every %s qualifying batches → group+traits refine | (4) repeat — iter %s/%s",
            args.jsd_threshold,
            args.min_posts_for_feedback_batch,
            args.batch_size,
            args.evolve_every_n_batches,
            iteration,
            args.num_iterations,
        )
        _echo(
            "[SUMMARY] group preview: %r...",
            (state.get("group_summary") or "")[:220],
        )

        processed: set[str] = set()
        state["batch_seq"] = 0
        refine_accumulator: Dict[str, Any] = {}
        pred_snapshot_iter_start = build_prediction_snapshot_for_memory(user_pred_map)

        pending = list(all_posts)
        if args.max_posts > 0:
            pending = pending[: args.max_posts]
        n_posts = len(pending)
        iteration_jsds: List[float] = []
        all_feedback_delta_rows.clear()
        _echo(
            "[ITER %s] Sweeping %s posts | batch_size=%s | min_posts_for_feedback=%s | "
            "refine after %s full feedback batches | JSD threshold=%s | stats -> %s",
            iteration,
            n_posts,
            args.batch_size,
            args.min_posts_for_feedback_batch,
            args.evolve_every_n_batches,
            args.jsd_threshold,
            iteration_stats_path.name,
        )

        for i in range(0, n_posts, args.batch_size):
            batch = pending[i : i + args.batch_size]
            feedback_ok = len(batch) >= args.min_posts_for_feedback_batch
            batch_key = (
                memory_batch_key(iteration, int(state["batch_seq"]))
                if feedback_ok
                else f"iter{iteration}_traceonly_offset{i}"
            )
            post_ids = [p.get("post_id") for _, _, p in batch]
            _echo(
                "[BATCH] iter=%s key=%s | posts %s–%s of %s | feedback_memory=%s | ids=%s",
                iteration,
                batch_key,
                i + 1,
                min(i + len(batch), n_posts),
                n_posts,
                "yes" if feedback_ok else "NO (<%s posts — traces+preds only)" % args.min_posts_for_feedback_batch,
                post_ids,
            )

            batch_review_idx_key: List[Tuple[int, str]] = []
            for pid, b, p in batch:
                _pk = (str(pid), str(p.get("post_id") or ""))
                batch_review_idx_key.append(
                    post_key_to_meta.get(_pk, (0, f"{pid}_review_0")),
                )
            results = await asyncio.gather(
                *[
                    process_one(iteration, pid, b, p, batch_review_idx_key[j][0], batch_review_idx_key[j][1])
                    for j, (pid, b, p) in enumerate(batch)
                ]
            )

            for r in results:
                ln = r.get("line")
                if isinstance(ln, dict) and ln.get("jsd") is not None:
                    iteration_jsds.append(float(ln["jsd"]))

            reviews: List[str] = []
            analyses: List[str] = []
            cats_in_batch: List[str] = []
            for (pid, _bundle, post), r in zip(batch, results):
                c = str(post.get("category") or "").strip()
                if c:
                    cats_in_batch.append(c)
                d = r.get("delta")
                if d:
                    reviews.append(linkedin_review_key(pid, post.get("post_id")))
                    analyses.append(d)
            if (
                feedback_ok
                and reviews
                and analyses
                and len(reviews) == len(analyses)
                and getattr(args, "linkedin_root_cause_memory", True)
            ):
                rc_items: List[Dict[str, Any]] = []
                for j, (pid, _bundle, post) in enumerate(batch):
                    r = results[j]
                    d = r.get("delta")
                    if not d:
                        continue
                    ridx, _rkey = batch_review_idx_key[j]
                    entry = user_pred_map.get(pid, {}).get(ridx) or {}
                    pred = resolve_failed_prediction_for_memory_batch(
                        profile_id=str(pid),
                        review_idx=int(ridx),
                        entry=entry if isinstance(entry, dict) else {},
                        snapshot_before_current_iteration=pred_snapshot_iter_start,
                    )
                    gt = {k: float(v) for k, v in (post.get("topic_probabilities") or {}).items()}
                    topics = list(post.get("topics_ordered") or [])
                    body = (post.get("body_text") or "").strip()
                    stim = (post.get("stimulus") or "").strip()
                    syn = str(pred.get("review_text") or "").strip()
                    if not syn:
                        syn = "(no synthetic post in this mode — topic distribution prediction only)"
                    rc_items.append(
                        {
                            "review_key": linkedin_review_key(pid, post.get("post_id")),
                            "category": str(post.get("category") or ""),
                            "stimulus": stim,
                            "actual_review_text": body,
                            "failed_prediction_text": syn,
                            "topics_ordered": topics,
                            "predicted_themes": pred.get("predicted_themes")
                            if isinstance(pred.get("predicted_themes"), dict)
                            else {},
                            "ground_truth_topic_probabilities": gt,
                            "preliminary_delta_notes": str(d),
                        }
                    )
                if len(rc_items) == len(analyses):
                    rc_model = (getattr(args, "linkedin_root_cause_model", "") or "").strip() or args.delta_model
                    analyses = await linkedin_memory_batch_root_cause_analyses(
                        client,
                        rc_model,
                        state=state,
                        memory=memory,
                        pending_memory_keys=set(),
                        review_items=rc_items,
                        fallback_analyses=analyses,
                    )
            uniq_cats = sorted(set(cats_in_batch))
            if len(uniq_cats) == 1:
                category_field = uniq_cats[0]
            elif uniq_cats:
                category_field = " | ".join(uniq_cats)
            else:
                category_field = ""

            for r in results:
                pid = r.get("post_id")
                if pid:
                    processed.add(str(pid))

            if feedback_ok:
                refine_accumulator[batch_key] = {
                    "reviews": reviews,
                    "analyses": analyses,
                    "category": category_field,
                }
                memory[batch_key] = refine_accumulator[batch_key]
                save_memory(memory_path, memory)
                state["batch_seq"] = int(state["batch_seq"]) + 1
            else:
                _warn(
                    "[BATCH] Skipping memory + refine window for this chunk (%s posts < %s).",
                    len(batch),
                    args.min_posts_for_feedback_batch,
                )

            state["processed_post_ids"] = sorted(processed)
            state["last_completed_iteration"] = iteration
            _persist_state(
                f"after_batch_{batch_key}" if feedback_ok else f"after_traceonly_{batch_key}"
            )

            _echo(
                "[BATCH] %s done | high-JSD deltas in batch=%s / %s | accumulator batches=%s",
                batch_key,
                len(analyses),
                len(batch),
                len(refine_accumulator),
            )

            is_last_batch = i + len(batch) >= n_posts
            should_refine = len(refine_accumulator) >= args.evolve_every_n_batches or (
                is_last_batch and bool(refine_accumulator)
            )
            if should_refine:
                before = state.get("group_summary") or ""
                before_wc = _word_count(before)
                _echo(
                    "[REFINE] Triggered | windows=%s | current group_summary=%s words",
                    len(refine_accumulator),
                    before_wc,
                )
                logs_for_llm = copy.deepcopy(refine_accumulator)
                refine_accumulator.clear()
                new_summary = await refine_group_summary(
                    client,
                    args.refine_model,
                    before,
                    baseline_group,
                    memory,
                    logs_for_llm,
                    args.refine_memory_batches,
                )
                if new_summary:
                    refined_wc = _word_count(new_summary)
                    state["refinement_call_count"] = int(state.get("refinement_call_count") or 0) + 1
                    rc = state["refinement_call_count"]
                    state["group_summary"] = new_summary
                    _persist_state(f"after_group_refine_{rc}")
                    _echo(
                        "[REFINE] SUCCESS call #%s | words %s → %s | preview: %r...",
                        rc,
                        before_wc,
                        refined_wc,
                        new_summary[:220],
                    )

                    target_wc = compute_shrink_target_words(
                        new_summary,
                        baseline_group,
                        floor_words=args.shrink_floor_words,
                        hard_max_words=args.shrink_hard_max_words,
                        baseline_ceiling_ratio=args.shrink_baseline_ratio,
                    )
                    _echo(
                        "[SHRINK] mandatory | target_max_words=%s (baseline=%s words)",
                        target_wc,
                        _word_count(baseline_group),
                    )
                    shrunk = await shrink_group_summary(
                        client,
                        args.shrink_model,
                        new_summary,
                        baseline_group,
                        target_wc,
                    )
                    if not shrunk:
                        shrunk = hard_truncate_words(new_summary, target_wc)
                        _warn(
                            "[SHRINK] LLM failed or empty — hard-truncated to %s words.",
                            target_wc,
                        )
                    final_wc = _word_count(shrunk)
                    state["group_summary"] = shrunk.strip()
                    state["shrink_call_count"] = int(state.get("shrink_call_count") or 0) + 1
                    _persist_state(f"after_group_shrink_{state['shrink_call_count']}")
                    _echo(
                        "[SHRINK] DONE call #%s | words %s → %s | preview: %r...",
                        state["shrink_call_count"],
                        refined_wc,
                        final_wc,
                        shrunk[:220],
                    )

                    if persona_evolver is not None:
                        analyses_blob = build_refine_analyses_text(
                            memory, logs_for_llm, args.refine_memory_batches
                        )
                        merged_for_qual = merge_batch_logs_dict(
                            memory, logs_for_llm, args.refine_memory_batches
                        )
                        q_base = state.get("baseline_qualitative_summary") or empty_qualitative_summary()
                        q_cur = copy.deepcopy(state.get("qualitative_summary") or empty_qualitative_summary())

                        def _qual_refine() -> Optional[Dict[str, Any]]:
                            return persona_evolver.run_single_refinement_step(
                                merged_for_qual,
                                q_cur,
                                q_base,
                                batch_analyses_text=analyses_blob,
                            )

                        _wk = sort_memory_batch_keys(list(logs_for_llm.keys()))
                        _qual_next = int(state.get("qual_refinement_call_count") or 0) + 1
                        if _wk:
                            _echo(
                                "  [Persona] Refinement step %s: refining on batch keys %s .. %s.",
                                _qual_next,
                                _wk[0],
                                _wk[-1],
                            )
                        else:
                            _echo(
                                "  [Persona] Refinement step %s: refining (empty window keys — using merged memory).",
                                _qual_next,
                            )
                        _echo("[QUAL_REFINE] persona schema (rationalized traits) from same memory window...")
                        refined_q = await asyncio.to_thread(_qual_refine)
                        if refined_q:
                            state["qualitative_summary"] = refined_q
                            state["qual_refinement_call_count"] = int(
                                state.get("qual_refinement_call_count") or 0
                            ) + 1
                            qrc = state["qual_refinement_call_count"]
                            _persist_state(f"after_qual_refine_{qrc}")
                            _echo(
                                "  [Persona] Refinement OK | call #%s | traits=%s (5-category schema)",
                                qrc,
                                sum(
                                    len((refined_q.get(k) or []))
                                    for k in (
                                        "skills_and_expertise",
                                        "working_style",
                                        "motivations_and_values",
                                        "pain_points_and_needs",
                                        "org_leadership_and_psychographic_profile",
                                    )
                                    if isinstance(refined_q.get(k), list)
                                ),
                            )
                            if (
                                qrc > 0
                                and qrc % args.qual_shrink_every_n_refinements == 0
                            ):
                                _echo(
                                    "  [Persona] Shrinking after %s qualitative refinement steps.",
                                    qrc,
                                )

                                def _qual_shrink() -> Optional[Dict[str, Any]]:
                                    return persona_evolver.shrink_step(state["qualitative_summary"])

                                shrunk_q = await asyncio.to_thread(_qual_shrink)
                                if shrunk_q:
                                    state["qualitative_summary"] = shrunk_q
                                    state["qual_shrink_call_count"] = int(
                                        state.get("qual_shrink_call_count") or 0
                                    ) + 1
                                    _persist_state(f"after_qual_shrink_{state['qual_shrink_call_count']}")
                                    _echo(
                                        "  [Persona] Shrink OK | call #%s | traits=%s (5-category schema)",
                                        state["qual_shrink_call_count"],
                                        sum(
                                            len((shrunk_q.get(k) or []))
                                            for k in (
                                                "skills_and_expertise",
                                                "working_style",
                                                "motivations_and_values",
                                                "pain_points_and_needs",
                                                "org_leadership_and_psychographic_profile",
                                            )
                                            if isinstance(shrunk_q.get(k), list)
                                        ),
                                    )
                                else:
                                    _warn("[QUAL_SHRINK] LLM failed; keeping post-refine persona JSON.")
                            # Deprecated: the old extra pass that rewrote description.json-style bullets.
                            # Trait texts are refined directly in the 5-category qualitative_summary schema.
                        else:
                            _warn("[QUAL_REFINE] LLM failed; keeping previous qualitative_summary.")
                else:
                    _warn("[REFINE] FAILED — keeping previous group_summary (%s words).", before_wc)

        js = iteration_jsds
        thr = float(args.jsd_threshold)
        stats_record: Dict[str, Any] = {
            "iteration": iteration,
            "num_iterations_planned": args.num_iterations,
            "n_posts_in_sweep": n_posts,
            "n_posts_with_jsd": len(js),
            "mean_jsd": round(statistics.mean(js), 6) if js else None,
            "median_jsd": round(statistics.median(js), 6) if js else None,
            "stdev_jsd": round(statistics.stdev(js), 6) if len(js) > 1 else None,
            "min_jsd": round(min(js), 6) if js else None,
            "max_jsd": round(max(js), 6) if js else None,
            "n_jsd_above_threshold": sum(1 for x in js if x > thr),
            "jsd_threshold": thr,
            "refinement_call_count_end": state.get("refinement_call_count"),
            "shrink_call_count_end": state.get("shrink_call_count"),
            "qual_refinement_call_count_end": state.get("qual_refinement_call_count"),
            "qual_shrink_call_count_end": state.get("qual_shrink_call_count"),
        }
        iteration_stats_path.parent.mkdir(parents=True, exist_ok=True)
        with open(iteration_stats_path, "a", encoding="utf-8") as sf:
            sf.write(json.dumps(stats_record, ensure_ascii=False) + "\n")
        if js:
            _echo(
                "[ITER %s] JSD | mean=%.6f median=%.6f min=%.6f max=%.6f | n=%s above_thr=%s (thr=%s) | -> %s",
                iteration,
                statistics.mean(js),
                statistics.median(js),
                min(js),
                max(js),
                len(js),
                stats_record["n_jsd_above_threshold"],
                thr,
                iteration_stats_path.name,
            )
        _echo("[ITER %s] COMPLETE | refinements so far=%s", iteration, state.get("refinement_call_count"))

    _log_banner("ALL ITERATIONS COMPLETE")
    _echo(
        "[DONE] state=%s | memory=%s | traces=%s | iter_stats=%s | final group=%s words | "
        "group_refine=%s group_shrink=%s | qual_refine=%s qual_shrink=%s",
        state_path,
        memory_path,
        trace_path,
        iteration_stats_path.name,
        _word_count(state.get("group_summary") or ""),
        state.get("refinement_call_count"),
        state.get("shrink_call_count"),
        state.get("qual_refinement_call_count"),
        state.get("qual_shrink_call_count"),
    )


def default_concurrent_requests() -> int:
    raw = (
        os.environ.get("OPENAI_TIER1_CONCURRENT") or os.environ.get("OPENAI_CONCURRENT") or "24"
    ).strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 24


def main() -> None:
    default_ctx = (
        REPO_ROOT
        / "scripts/LInkedin_Category_Topic_Extraction/tiered_posts_grouped/contextual_stimulus_extraction_with_topics.json"
    )
    default_work = SCRIPTS_SGO / "outputs" / "linkedin_tier1_sgo" / "default_run"
    default_description = REPO_ROOT / "scripts/Linkedin_Posts_Extraction/raw_profiles/description.json"
    default_profiles = REPO_ROOT / "scripts/Linkedin_Posts_Extraction/raw_profiles/profiles"
    default_mapping = (
        REPO_ROOT
        / "scripts/LInkedin_Category_Topic_Extraction/tiered_posts_grouped/discovered_category_topic_mapping.json"
    )

    p = argparse.ArgumentParser(
        description="LinkedIn tier-1: JSD vs GT, delta; evolve group prose + qualitative persona (verbose logs)."
    )
    p.add_argument("--contextual-json", type=str, default=str(default_ctx))
    p.add_argument("--description-json", type=str, default=str(default_description))
    p.add_argument(
        "--mapping-json",
        type=str,
        default=str(default_mapping),
        help="Recorded in delta_style meta (optional; file need not exist)",
    )
    p.add_argument("--profiles-root", type=str, default=str(default_profiles))
    p.add_argument("--work-dir", type=str, default=str(default_work))
    p.add_argument(
        "--initial-group-summary",
        type=str,
        default="",
        help="Optional file: plain text OR JSON {\"summary\":\"...\"} to override starting group summary",
    )
    p.add_argument("--num-iterations", type=int, default=5, help="Last iteration index to run (inclusive)")
    p.add_argument(
        "--start-iteration",
        type=int,
        default=1,
        help="First iteration index (default 1). Use 3 with --num-iterations 5 to run sweeps 3,4,5 only.",
    )
    p.add_argument("--model", type=str, default=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"))
    p.add_argument("--delta-model", type=str, default=os.environ.get("OPENAI_DELTA_MODEL", "gpt-4o-mini"))
    p.add_argument(
        "--linkedin-root-cause-model",
        type=str,
        default="",
        dest="linkedin_root_cause_model",
        help="Model for batch memory root-cause prompt (default: same as --delta-model)",
    )
    _rc = p.add_mutually_exclusive_group()
    _rc.add_argument(
        "--linkedin-root-cause-memory",
        dest="linkedin_root_cause_memory",
        action="store_true",
        help="Use LinkedIn batch root-cause prompt for memory analyses (default on)",
    )
    _rc.add_argument(
        "--no-linkedin-root-cause-memory",
        dest="linkedin_root_cause_memory",
        action="store_false",
        help="Legacy: per-post delta_bridge_analysis strings in memory/traces (disables batch root-cause prompt)",
    )
    _rc.set_defaults(linkedin_root_cause_memory=True)
    p.add_argument(
        "--refine-model",
        type=str,
        default=os.environ.get("OPENAI_REFINE_MODEL", "gpt-4o"),
        help="Model for rewriting group_summary",
    )
    p.add_argument(
        "--refine-memory-batches",
        type=int,
        default=12,
        help="How many prior memory batch keys to include (sorted) when refining (lower = smaller prompt)",
    )
    p.add_argument(
        "--shrink-model",
        type=str,
        default=os.environ.get("OPENAI_SHRINK_MODEL", "gpt-4o"),
        help="Model for mandatory compress step after each refine",
    )
    p.add_argument(
        "--shrink-baseline-ratio",
        type=float,
        default=1.08,
        help="Word ceiling ≈ baseline_word_count * ratio (capped by --shrink-hard-max-words)",
    )
    p.add_argument(
        "--shrink-floor-words",
        type=int,
        default=200,
        help="Never shrink ceiling below this many words",
    )
    p.add_argument(
        "--shrink-hard-max-words",
        type=int,
        default=1200,
        help="Absolute max words for shrink target ceiling",
    )
    p.add_argument(
        "--qual-schema-model",
        type=str,
        default=os.environ.get("OPENAI_QUAL_SCHEMA_MODEL", "gpt-4o"),
        help="OpenAI model for qualitative_summary refine + shrink (sync API in thread)",
    )
    p.add_argument(
        "--qual-shrink-every-n-refinements",
        type=int,
        default=5,
        help="After this many successful qualitative refinements, run persona shrink",
    )
    p.add_argument(
        "--disable-qual-schema",
        action="store_true",
        help="Do not evolve qualitative_summary (group summary pipeline unchanged)",
    )
    p.add_argument(
        "--disable-trait-descriptions-refine",
        action="store_true",
        help="Skip the extra LLM pass that rewrites description.json-style trait *descriptions* bullets",
    )
    p.add_argument(
        "--max-qual-json-chars",
        type=int,
        default=10000,
        help="Max chars of qualitative_summary JSON injected into per-post prediction context",
    )
    p.add_argument(
        "--disable-evolution-state-snapshots",
        action="store_true",
        help="Skip evolution/state_history/NNNNNN_*.json (only write evolution/evolution_state.json)",
    )
    _sync_desc = p.add_mutually_exclusive_group()
    _sync_desc.add_argument(
        "--sync-description-json",
        dest="sync_description_json",
        action="store_true",
        help="Update <work-dir>/description.json (summary + traits) whenever state is saved; seed --description-json is read-only (default).",
    )
    _sync_desc.add_argument(
        "--no-sync-description-json",
        dest="sync_description_json",
        action="store_false",
        help="Leave <work-dir>/description.json unchanged; only evolution state is updated.",
    )
    _djson = p.add_mutually_exclusive_group()
    _djson.add_argument(
        "--write-delta-style-predictions-json",
        dest="write_delta_style_predictions_json",
        action="store_true",
        help="Rewrite <work-dir>/delta_method_predictions.json on each state save (same keys as tier1_delta_method_predictions).",
    )
    _djson.add_argument(
        "--no-delta-style-predictions-json",
        dest="write_delta_style_predictions_json",
        action="store_false",
        help="Skip delta_method_predictions.json (traces + evolution_state only).",
    )
    p.set_defaults(sync_description_json=True, write_delta_style_predictions_json=True)
    p.add_argument(
        "--delta-style-predictions-output",
        type=str,
        default="",
        help="Path for delta-style JSON (default: <work-dir>/delta_method_predictions.json)",
    )
    p.add_argument(
        "--save-all-reviews-deltas",
        action="store_true",
        help="On each state save, also write work_dir/linkedin_tier1_all_reviews_deltas.json (feedback-loop rows)",
    )
    p.add_argument(
        "--gt-prob-threshold",
        type=float,
        default=0.5,
        help="GT recall metrics threshold (delta script parity); full GT probs used for JSD when logprobs.",
    )
    p.add_argument(
        "--full-delta-post-pipeline",
        action="store_true",
        help="Per post: run i0 (run_audience_room_i0_for_post) + tier1_delta correction rounds + correction_journey "
        "using the same evolving group_summary / qualitative_summary as this loop. "
        "Memory, evolution/ state files, refines, and description.json sync are unchanged. "
        "Ordering note: batch Part B (root-cause memory) runs after the per-post gather for each sweep chunk, "
        "so Part A corrections still precede that batch in this script; "
        "tier1_delta_method_predictions flushes Part B memory batches before Part A within each i1+ chunk.",
    )
    p.add_argument(
        "--correction-iterations",
        type=int,
        default=5,
        help="Max i1..iN correction rounds when --full-delta-post-pipeline (same as tier1_delta_method_predictions).",
    )
    p.add_argument(
        "--correction-delta-threshold",
        type=float,
        default=None,
        help="Stop corrections when theme/overall delta <= this (default: settings.DELTA_THRESHOLD).",
    )
    p.add_argument(
        "--correction-feedback-on",
        choices=("theme", "overall"),
        default="theme",
        help="Which delta gates i1.. when --full-delta-post-pipeline (same as tier1_delta --feedback-on).",
    )
    p.add_argument(
        "--topic-model",
        type=str,
        default=os.environ.get("OPENAI_TOPIC_MODEL", "gpt-4o-mini"),
        help="Topic Yes/No model when --full-delta-post-pipeline (tier1_delta --topic-model).",
    )
    p.add_argument(
        "--prediction-include-post-body",
        action="store_true",
        help="Include post body in topic Yes/No prompts (leaks GT surface; default OFF for SGO)",
    )
    p.add_argument("--jsd-threshold", type=float, default=0.2)
    p.add_argument("--batch-size", type=int, default=5, help="Posts processed together per sweep step")
    p.add_argument(
        "--min-posts-for-feedback-batch",
        type=int,
        default=5,
        help="Chunks smaller than this skip memory + refine-window (predictions still run + traced)",
    )
    p.add_argument(
        "--evolve-every-n-batches",
        type=int,
        default=5,
        help="After this many **full** feedback batches (each ≥ min-posts) accumulate, run refine+shrink",
    )
    p.add_argument(
        "--concurrent",
        type=int,
        default=default_concurrent_requests(),
        help="Max in-flight topic Yes/No API calls (global semaphore). Default from env "
        "OPENAI_TIER1_CONCURRENT or OPENAI_CONCURRENT, else 24; pass e.g. --concurrent 48 if quota allows.",
    )
    p.add_argument("--prob-threshold", type=float, default=0.5)
    p.add_argument("--max-retries", type=int, default=3)
    p.add_argument("--retry-delay", type=float, default=1.0)
    p.add_argument("--max-text-chars", type=int, default=12000)
    p.add_argument("--max-group-chars", type=int, default=12000)
    p.add_argument("--max-individual-chars", type=int, default=6000)
    p.add_argument("--max-posts", type=int, default=0, help="Cap posts per iteration (0 = all)")
    args = p.parse_args()
    asyncio.run(run(args))


