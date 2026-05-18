#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Dict

SCRIPTS_SGO = Path(__file__).resolve().parent.parent
if str(SCRIPTS_SGO) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_SGO))

import _package_setup  # noqa: F401

from openai import OpenAI

from sgo_training.config import settings
from sgo_training.utils import io_utils
from sgo_training.i0_initial_deltas import compute_and_save_i0_deltas_for_micro_cluster


def _load_category_mapping(path: str) -> Dict[str, Any]:
    p = Path(path).expanduser().resolve()
    data = io_utils.load_json_file(str(p), {})
    if isinstance(data, dict) and "category_to_main_mapping" in data:
        return data
    return data if isinstance(data, dict) else {}


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Compute i0 initial deltas (no training iterations) for all micro-cluster files."
    )
    ap.add_argument("--prediction-input-dir", required=True, help="Directory containing cluster_*/ micro_*.json files")
    ap.add_argument("--themes-file", required=True, help="Topic universe / themes json file path")
    ap.add_argument("--output-base-dir", required=True, help="Base output directory (delta/cache/failures will be created under it)")
    ap.add_argument("--category-mapping-json", required=True, help="Category mapping json path")
    ap.add_argument("--scoring-mode", default="confidence", choices=["confidence", "logprobs", "logprobs-without-persona-context"])
    ap.add_argument("--embedding-model", default=None, help="Override settings.EMBEDDING_MODEL")
    args = ap.parse_args()

    if not os.environ.get("OPENAI_API_KEY"):
        print("ERROR: OPENAI_API_KEY is not set.", file=sys.stderr)
        return 2

    settings.SCORING_MODE = args.scoring_mode
    if args.embedding_model:
        settings.EMBEDDING_MODEL = str(args.embedding_model)

    settings.PATHS["PREDICTION_INPUT_DIR"] = str(Path(args.prediction_input_dir).expanduser().resolve())
    settings.PATHS["THEMES_FILE"] = str(Path(args.themes_file).expanduser().resolve())
    settings.PATHS["OUTPUT_BASE_DIR"] = str(Path(args.output_base_dir).expanduser().resolve())

    settings.PATHS["OUTPUT_DIRS"] = {
        "corrected": settings.PATHS["OUTPUT_BASE_DIR"],
        "cache": os.path.join(settings.PATHS["OUTPUT_BASE_DIR"], "_cache"),
        "memory": os.path.join(settings.PATHS["OUTPUT_BASE_DIR"], "_memory"),
        "journey": os.path.join(settings.PATHS["OUTPUT_BASE_DIR"], "_journey"),
        "delta": os.path.join(settings.PATHS["OUTPUT_BASE_DIR"], "_delta"),
        "failures": os.path.join(settings.PATHS["OUTPUT_BASE_DIR"], "_failures"),
    }
    for p in settings.PATHS["OUTPUT_DIRS"].values():
        os.makedirs(p, exist_ok=True)

    category_mapping = _load_category_mapping(args.category_mapping_json)
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], base_url=os.environ.get("OPENAI_BASE_URL"))

    files = io_utils.discover_all_micro_cluster_files(settings.PATHS["PREDICTION_INPUT_DIR"])
    if not files:
        print(f"No micro cluster files found under {settings.PATHS['PREDICTION_INPUT_DIR']}", file=sys.stderr)
        return 1

    ok = 0
    for cluster_id, micro_id, fp in files:
        out_d, _fail = compute_and_save_i0_deltas_for_micro_cluster(
            filepath=fp,
            client=client,
            category_mapping=category_mapping,
            output_cluster_id=cluster_id,
            output_micro_id=micro_id,
        )
        ok += 1
        print(f"[i0] {cluster_id}/{micro_id} -> {out_d}", flush=True)

    print(f"Done. Computed i0 deltas for {ok} files.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

