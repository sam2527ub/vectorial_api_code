#!/usr/bin/env python3
"""
Tier 2 — category extraction only (delegates to ``extract_stimulus_category.extract_tier2``).

**Input (default):** ``tiered_posts_grouped/filtered_kept_tier2.json`` (after ``filter_1.py --tier 2``).
Do **not** read raw ``tier_2_reposts_and_comments.json`` here — that bypasses the filter and
includes rows that were dropped (e.g. 374 vs 269 kept).

Output shape: ``post_id``, ``body_text`` (reply), ``stimulus`` (passthrough from input), ``category`` (LLM).

Run:
  python extract_tier2_interaction_category.py
  python extract_tier2_interaction_category.py --output tiered_posts_grouped/tier2_interaction_category.json

Then ground truth:
  python post_topic_classification/contextual_ground_truth_extraction.py --preset tier2
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from extract_stimulus_category import (  # noqa: E402
    EXTRACTION_GROUPED,
    extract_tier2,
    resolve_category_mapping_path,
)

DEFAULT_INPUT = EXTRACTION_GROUPED / "filtered_kept_tier2.json"
DEFAULT_OUTPUT = EXTRACTION_GROUPED / "contextual_stimulus_extraction_tier2.json"


def main() -> None:
    p = argparse.ArgumentParser(
        description=(
            "Tier 2 category-only extraction from filtered_kept_tier2.json "
            "(stimulus passthrough; same logic as extract_stimulus_category.py --tier 2)."
        )
    )
    p.add_argument(
        "--input",
        type=str,
        default=str(DEFAULT_INPUT),
        help=f"Filtered tier-2 JSON (default: {DEFAULT_INPUT.name}). Run filter_1.py --tier 2 first.",
    )
    p.add_argument(
        "--output",
        type=str,
        default=str(DEFAULT_OUTPUT),
        help=f"Output path (default: {DEFAULT_OUTPUT.name}).",
    )
    p.add_argument(
        "--mapping",
        type=str,
        default=None,
        help="Category mapping JSON (default: tier-2 discovered if present, else tier-1).",
    )
    args = p.parse_args()

    tier_path = Path(args.input).expanduser().resolve()
    out_path = Path(args.output).expanduser().resolve()
    mapping_arg = Path(args.mapping).expanduser().resolve() if args.mapping else None

    try:
        mapping_path = resolve_category_mapping_path(mapping_arg, tier="2")
    except FileNotFoundError as e:
        print(f"❌ {e}")
        return

    if not tier_path.is_file():
        print(f"❌ Tier-2 input not found: {tier_path}")
        print("   Run: python filter_1.py --tier 2")
        return

    print("⚙️ Tier 2 — category only (LLM); stimulus = passthrough from filtered input")
    print(f"   Input: {tier_path}")
    print(f"   Output: {out_path}")
    print(f"   Mapping: {mapping_path}")
    extract_tier2(tier_path, out_path, mapping_path)


if __name__ == "__main__":
    main()
