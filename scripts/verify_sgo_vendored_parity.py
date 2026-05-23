#!/usr/bin/env python3
"""Fail if production vendored_sgo diverges from scripts/scripts_sgo (prompts + linkedin core)."""

from __future__ import annotations

import filecmp
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "scripts" / "scripts_sgo"
DEST = ROOT / "app" / "services" / "linkedin_sgo_pipeline_service_async" / "vendored_sgo"

LINKEDIN_FILES = [
    "tier1_delta_method_predictions.py",
    "tier1_sgo_feedback_loop.py",
    "i0_audience_room_helpers.py",
    "i0_linkedin_context.py",
    "i0_initial_prediction.py",
    "__init__.py",
]

DIRS = [
    "prompts",
    "config",
    "llm",
    "utils",
    "calculate_behaviour_loss",
    "feedback_loop",
    "generate_synthetic_review_and_memory_analysis",
    "sgo_training",
]


def main() -> int:
    errors: list[str] = []

    for name in ("_paths.py", "_package_setup.py"):
        a, b = SRC / name, DEST / name
        if not a.is_file() or not b.is_file() or not filecmp.cmp(a, b, shallow=False):
            errors.append(name)

    for fn in LINKEDIN_FILES:
        a, b = SRC / "linkedin" / fn, DEST / "linkedin" / fn
        if not a.is_file():
            errors.append(f"missing source linkedin/{fn}")
        elif not b.is_file() or not filecmp.cmp(a, b, shallow=False):
            errors.append(f"linkedin/{fn}")

    for d in DIRS:
        src_d = SRC / d
        dest_d = DEST / d
        if not src_d.is_dir():
            continue
        for p in sorted(src_d.rglob("*")):
            if p.is_dir() or "__pycache__" in p.parts or p.suffix == ".pyc":
                continue
            rel = p.relative_to(src_d)
            other = dest_d / rel
            if not other.is_file() or not filecmp.cmp(p, other, shallow=False):
                errors.append(f"{d}/{rel}")

    if errors:
        print("PARITY CHECK FAILED — run: bash scripts/sync_linkedin_sgo_vendored.sh", file=sys.stderr)
        for e in errors[:40]:
            print(f"  - {e}", file=sys.stderr)
        if len(errors) > 40:
            print(f"  ... and {len(errors) - 40} more", file=sys.stderr)
        return 1

    print("OK: scripts/scripts_sgo matches vendored_sgo for production paths.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
