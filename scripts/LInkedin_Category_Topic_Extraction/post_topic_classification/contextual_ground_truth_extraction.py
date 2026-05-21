#!/usr/bin/env python3
"""
Local **ground-truth** per-topic extraction (LLM Yes/No + logprobs) for contextual JSON.

All default paths and preset come from ``ground_truth_extraction_local.yaml`` (or
``GROUND_TRUTH_LOCAL_CONFIG``). Service defaults: ``app/.../ground_truth_extraction_config.yaml``
(``GROUND_TRUTH_EXTRACTION_CONFIG``).
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[3] / ".env", override=True)
except ImportError:
    pass

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import yaml  # noqa: E402

from app.services.linkedin_ground_truth_extraction_service.ground_truth_extraction_config import (  # noqa: E402
    GroundTruthTierRetry,
    get_ground_truth_extraction_config,
)
from app.services.linkedin_ground_truth_extraction_service.ground_truth_extraction_core import (  # noqa: E402
    iter_mutable_posts,
    load_topics_mapping_from_path,
    normalize_contextual_payload,
    save_contextual_json,
)
from app.services.linkedin_ground_truth_extraction_service.ground_truth_extraction_processor import (  # noqa: E402
    run_ground_truth_extraction_on_payload,
    process_single_post as _process_single_post_impl,
)
from openai import AsyncOpenAI  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

_DEFAULT_LOCAL = Path(__file__).resolve().parent / "ground_truth_extraction_local.yaml"


def _load_local_config(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(
            f"Local config not found: {path}. Copy ground_truth_extraction_local.example.yaml."
        )
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _stimulus_categories(stimulus_path: Path) -> set[str]:
    raw = json.loads(stimulus_path.read_text(encoding="utf-8"))
    payload = normalize_contextual_payload(raw)
    cats: set[str] = set()
    for _uid, post in iter_mutable_posts(payload):
        c = (post.get("category") or "").strip()
        if c:
            cats.add(c)
    return cats


def _mapping_categories(mapping_path: Path) -> set[str]:
    data = json.loads(mapping_path.read_text(encoding="utf-8"))
    return {str(c.get("category", "")).strip() for c in (data.get("categories") or []) if c}


def _mapping_coverage(stimulus_cats: set[str], mapping_path: Path) -> float:
    if not stimulus_cats:
        return 1.0
    map_cats = _mapping_categories(mapping_path)
    if not map_cats:
        return 0.0
    return len(stimulus_cats & map_cats) / len(stimulus_cats)


def _resolve_mapping_path(
    configured: Path,
    stimulus_path: Path,
    *,
    preset: str,
    tiered_dir: Path,
) -> Path:
    """Use configured mapping when it covers stimulus categories; else try tier fallbacks."""
    stim_cats = _stimulus_categories(stimulus_path)
    if not stim_cats:
        return configured
    if configured.is_file() and _mapping_coverage(stim_cats, configured) >= 1.0:
        return configured

    fallbacks: List[Path] = []
    if preset == "tier2":
        fallbacks = [
            tiered_dir / "category_topic_mapping.json",
            tiered_dir / "discovered_category_topic_mapping_tier2.json",
            tiered_dir / "discovered_category_topic_mapping.json",
        ]
    else:
        fallbacks = [
            tiered_dir / "discovered_category_topic_mapping.json",
            tiered_dir / "category_topic_mapping.json",
        ]

    best: Tuple[float, Path] | None = None
    for cand in fallbacks:
        if not cand.is_file() or cand == configured:
            continue
        cov = _mapping_coverage(stim_cats, cand)
        if cov <= 0:
            continue
        if best is None or cov > best[0]:
            best = (cov, cand)

    if best and best[0] > (_mapping_coverage(stim_cats, configured) if configured.is_file() else 0):
        logger.warning(
            "Mapping %s does not cover stimulus categories %s; using %s (coverage %.0f%%).",
            configured.name,
            sorted(stim_cats),
            best[1].name,
            best[0] * 100,
        )
        return best[1]

    if configured.is_file():
        cov = _mapping_coverage(stim_cats, configured)
        if cov < 1.0:
            logger.warning(
                "Mapping %s only covers %.0f%% of stimulus categories %s; "
                "posts with unknown categories will be skipped.",
                configured.name,
                cov * 100,
                sorted(stim_cats),
            )
        return configured
    raise FileNotFoundError(f"Mapping not found: {configured}")


def _resolve_paths(
    local: Dict[str, Any],
    config_path: Path,
    *,
    preset_override: str | None = None,
    input_override: str | None = None,
    output_override: str | None = None,
    mapping_override: str | None = None,
) -> Tuple[Path, Path, Path, str, bool]:
    base_dir = config_path.parent
    preset = (preset_override or local.get("preset") or "tier1").strip().lower()
    if preset not in ("tier1", "tier2"):
        preset = "tier1"
    pgroup = (local.get("paths") or {}).get(preset) or {}

    def _p(k: str, override: str | None) -> Path:
        s = (override or pgroup.get(k) or "").strip()
        p = Path(str(s))
        if not p.is_absolute():
            p = (base_dir / p).resolve()
        return p

    inp = _p("input", input_override)
    outp = _p("output", output_override)
    mapp = _p("mapping", mapping_override)
    tiered_dir = (base_dir / "../tiered_posts_grouped").resolve()
    mapp = _resolve_mapping_path(mapp, inp, preset=preset, tiered_dir=tiered_dir)
    is_t2 = preset == "tier2"
    return inp, outp, mapp, preset, is_t2


def load_contextual_payload(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Expected object JSON: {path}")
    return normalize_contextual_payload(raw)


async def process_single_post(
    client: AsyncOpenAI,
    model: str,
    post: dict,
    topics: List[str],
    semaphore,  # asyncio.Semaphore
    prob_threshold: float,
    max_retries: int,
    retry_delay: float,
    max_text_chars: int,
    *,
    include_post_body: bool = True,
) -> dict:
    """SGO-compatible wrapper (``max_retries`` + ``retry_delay`` → service retry profile)."""
    gcfg = get_ground_truth_extraction_config()
    retry_cfg = GroundTruthTierRetry(
        max_attempts=int(max_retries),
        base_delay_sec=float(retry_delay),
        max_delay_sec=60.0,
        jitter_sec=0.35,
    )
    return await _process_single_post_impl(
        client=client,
        model=model,
        post=post,
        topics=topics,
        llm_semaphore=semaphore,
        prob_threshold=prob_threshold,
        max_text_chars=max_text_chars,
        per_call_timeout=gcfg.per_call_timeout_sec,
        retry_cfg=retry_cfg,
        include_post_body=include_post_body,
    )


async def run_from_local_config(
    local_path: Path,
    *,
    preset_override: str | None = None,
    input_override: str | None = None,
    output_override: str | None = None,
    mapping_override: str | None = None,
) -> None:
    local = _load_local_config(local_path)
    if not os.environ.get("OPENAI_API_KEY"):
        logger.error("OPENAI_API_KEY is not set")
        sys.exit(1)
    input_path, output_path, mapping_path, preset, is_t2 = _resolve_paths(
        local,
        local_path,
        preset_override=preset_override,
        input_override=input_override,
        output_override=output_override,
        mapping_override=mapping_override,
    )
    for label, p in (("input", input_path), ("mapping", mapping_path)):
        if not p.is_file():
            logger.error("Missing %s file: %s", label, p)
            sys.exit(1)
    logger.info(
        "Ground truth %s: input=%s mapping=%s output=%s",
        preset,
        input_path.name,
        mapping_path.name,
        output_path.name,
    )
    ovr: Dict[str, Any] = local.get("overrides") or {}
    if not isinstance(ovr, dict):
        ovr = {}

    topics = load_topics_mapping_from_path(mapping_path)
    if output_path.is_file():
        try:
            working = json.loads(output_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Could not read output; using input. (%s)", e)
            working = copy.deepcopy(json.loads(input_path.read_text(encoding="utf-8")))
    else:
        working = copy.deepcopy(json.loads(input_path.read_text(encoding="utf-8")))
    working = normalize_contextual_payload(working)
    gcfg = get_ground_truth_extraction_config()
    ollm = ovr.get("max_concurrent_llm")
    opst = ovr.get("max_concurrent_posts")
    mdl = str(ovr["model"]) if ovr.get("model") else os.environ.get("OPENAI_MODEL", gcfg.default_model)

    def _save_after_marks(w: dict) -> None:
        save_contextual_json(w, output_path)
        logger.info("Checkpoint after terminal marks → %s", output_path)

    mtc = ovr.get("max_posts_to_classify")
    await run_ground_truth_extraction_on_payload(
        working,
        topics,
        mdl,
        is_tier2=is_t2,
        max_concurrent_llm=int(ollm) if ollm is not None else None,
        max_concurrent_posts=int(opst) if opst is not None else None,
        max_posts_to_classify=int(mtc) if mtc is not None else None,
        on_after_terminal_marks=_save_after_marks,
    )
    w = normalize_contextual_payload(working)
    if isinstance(w.get("ground_truth_extraction_meta"), dict):
        w["ground_truth_extraction_meta"]["input_file"] = str(input_path)
        w["ground_truth_extraction_meta"]["mapping_file"] = str(mapping_path)
        w["ground_truth_extraction_meta"]["output_file"] = str(output_path)
        w["ground_truth_extraction_meta"]["local_config"] = str(local_path)
        w["ground_truth_extraction_meta"]["preset"] = preset
    save_contextual_json(w, output_path)
    logger.info("Wrote %s", output_path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ground-truth topic extraction; paths from ground_truth_extraction_local.yaml by default."
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to local YAML (default: GROUND_TRUTH_LOCAL_CONFIG or ground_truth_extraction_local.yaml next to this script).",
    )
    parser.add_argument(
        "--preset",
        choices=("tier1", "tier2"),
        default=None,
        help="tier1 or tier2 — overrides preset in YAML (e.g. --preset tier2).",
    )
    parser.add_argument("--input", type=str, default=None, help="Override stimulus JSON input path.")
    parser.add_argument("--output", type=str, default=None, help="Override ground-truth output JSON path.")
    parser.add_argument(
        "--mapping",
        type=str,
        default=None,
        help="Override category→topics mapping JSON (auto-fallback if categories do not match stimulus).",
    )
    args = parser.parse_args()
    p = args.config
    if p and str(p).strip():
        local = Path(p).expanduser().resolve()
    else:
        envp = os.environ.get("GROUND_TRUTH_LOCAL_CONFIG", "").strip()
        local = Path(envp).expanduser().resolve() if envp else _DEFAULT_LOCAL
    asyncio.run(
        run_from_local_config(
            local,
            preset_override=args.preset,
            input_override=args.input,
            output_override=args.output,
            mapping_override=args.mapping,
        )
    )


if __name__ == "__main__":
    main()
