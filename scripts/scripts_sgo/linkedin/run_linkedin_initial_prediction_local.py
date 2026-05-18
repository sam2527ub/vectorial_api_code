#!/usr/bin/env python3
"""
Local initial prediction (i0): synthetic post + predicted topics + deltas vs ground truth.

Reads a contextual JSON with ``topic_probabilities`` (e.g. ``ground_truth_extraction_tier2.json``),
``description.json``, per-profile ``profile.json`` under ``--profiles-root``, and category→topics mapping.

Usage (from repo root)::

  PYTHONPATH=. python scripts/scripts_sgo/linkedin/run_linkedin_initial_prediction_local.py --tier 2

Output is compatible with ``tier1_delta_method_predictions.py --precomputed-i0-json``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS_SGO = Path(__file__).resolve().parent.parent
_TIERED = _REPO_ROOT / "scripts/LInkedin_Category_Topic_Extraction/tiered_posts_grouped"
_RAW = _REPO_ROOT / "scripts/Linkedin_Posts_Extraction/raw_profiles"

if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


def _default_paths(tier: int) -> dict[str, Path]:
    if tier == 2:
        return {
            "contextual": _TIERED / "ground_truth_extraction_tier2.json",
            "mapping": _TIERED / "discovered_category_topic_mapping_tier2.json",
            "output": _SCRIPTS_SGO / "outputs/linkedin_tier2_sgo/default_run/linkedin_i0_only.json",
            "mirror": _REPO_ROOT / "Predictions/linkedin_i0_tier2.json",
        }
    return {
        "contextual": _TIERED / "contextual_stimulus_extraction_with_topics.json",
        "mapping": _TIERED / "discovered_category_topic_mapping.json",
        "output": _SCRIPTS_SGO / "outputs/linkedin_tier1_sgo/default_run/linkedin_i0_only.json",
        "mirror": _REPO_ROOT / "Predictions/linkedin_i0_only.json",
    }


def main() -> None:
    defaults = _default_paths(1)
    p = argparse.ArgumentParser(description="LinkedIn initial prediction from local GT + profiles.")
    p.add_argument("--tier", type=int, default=2, choices=(1, 2))
    p.add_argument("--contextual-json", type=str, default=None, help="GT / contextual JSON with topic_probabilities.")
    p.add_argument("--mapping-json", type=str, default=None)
    p.add_argument("--description-json", type=str, default=str(_RAW / "description.json"))
    p.add_argument("--profiles-root", type=str, default=str(_RAW / "profiles"))
    p.add_argument("--output", type=str, default=None, help="Full artifact JSON path.")
    p.add_argument(
        "--mirror-predictions",
        type=str,
        default=None,
        help="Also write artifact here (default: Predictions/linkedin_i0_tier{N}.json).",
    )
    p.add_argument("--gen-model", type=str, default=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"))
    p.add_argument("--topic-model", type=str, default=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"))
    p.add_argument("--concurrent", type=int, default=8)
    p.add_argument("--max-posts", type=int, default=0, help="0 = all posts with GT.")
    p.add_argument("--checkpoint-every", type=int, default=5)
    p.add_argument("--no-seed-context-print", action="store_true")
    p.add_argument("--debug-i0-prompt", action="store_true")
    args = p.parse_args()

    tier = int(args.tier)
    defs = _default_paths(tier)
    ctx_path = Path(args.contextual_json or defs["contextual"]).expanduser().resolve()
    map_path = Path(args.mapping_json or defs["mapping"]).expanduser().resolve()
    desc_path = Path(args.description_json).expanduser().resolve()
    profiles_root = Path(args.profiles_root).expanduser().resolve()
    out_path = Path(args.output or defs["output"]).expanduser().resolve()
    mirror = (
        Path(args.mirror_predictions).expanduser().resolve()
        if args.mirror_predictions
        else defs["mirror"]
    )

    for label, path in (
        ("contextual", ctx_path),
        ("mapping", map_path),
        ("description", desc_path),
    ):
        if not path.is_file():
            logger.error("Missing %s: %s", label, path)
            sys.exit(1)
    if not profiles_root.is_dir():
        logger.error("Profiles root not found: %s", profiles_root)
        sys.exit(1)

    payload = json.loads(ctx_path.read_text(encoding="utf-8"))
    n_posts = sum(
        1
        for u in (payload.get("results_by_user") or {}).values()
        for post in (u.get("posts") or [])
        if isinstance(post, dict) and post.get("topic_probabilities")
    )
    logger.info(
        "tier=%s contextual=%s posts_with_GT=%s → %s",
        tier,
        ctx_path.name,
        n_posts,
        out_path,
    )

    from app.services.linkedin_initial_prediction_service.initial_prediction_processor import (
        run_initial_prediction,
    )
    from app.services.linkedin_initial_prediction_service.initial_prediction_types import (
        InitialPredictionRunParams,
    )

    params = InitialPredictionRunParams(
        tier=tier,
        contextual_payload=payload,
        description_seed_path=desc_path,
        mapping_path=map_path,
        profiles_root=profiles_root,
        output_path=out_path,
        mirror_repo_predictions_path=mirror,
        gen_model=args.gen_model,
        topic_model=args.topic_model,
        model_type_used="linkedin_i0_only",
        checkpoint_every=max(1, int(args.checkpoint_every)),
        concurrent=max(1, int(args.concurrent)),
        max_posts=int(args.max_posts),
        print_seed_context=not args.no_seed_context_print,
        debug_i0_prompt=args.debug_i0_prompt,
        s3_upload=False,
        s3_partial_upload=False,
    )

    async def _go():
        return await run_initial_prediction(params)

    try:
        result = asyncio.run(_go())
    except KeyboardInterrupt:
        logger.warning("Interrupted.")
        sys.exit(130)

    logger.info(
        "Done: ok=%s failed=%s of %s | wrote %s",
        result.posts_ok,
        result.posts_failed,
        result.posts_attempted,
        result.output_path_written,
    )
    if mirror:
        logger.info("Mirror: %s", mirror)


if __name__ == "__main__":
    main()
