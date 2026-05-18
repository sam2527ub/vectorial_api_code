from __future__ import annotations

import os
import time
import copy
from typing import Any, Dict, List, Optional, Tuple

from sgo_training.config import settings
from sgo_training.utils import io_utils
from sgo_training.utils import embeddings

from calculate_behaviour_loss.weighted_topic_review_metric_calculation import calculate_text_delta
from feedback_loop.batch_correction_pipeline import get_theme_delta


def _normalize_theme_map(theme_map_raw: Dict[str, Any]) -> Dict[str, List[str]]:
    theme_map: Dict[str, List[str]] = {}
    if not isinstance(theme_map_raw, dict):
        return {}

    for category, themes_data in theme_map_raw.items():
        if isinstance(themes_data, list):
            theme_map[str(category)] = [
                t.get("topic_name", t) if isinstance(t, dict) else str(t) for t in themes_data
            ]
        elif isinstance(themes_data, dict) and "topics" in themes_data:
            topics = themes_data.get("topics")
            if isinstance(topics, list):
                theme_map[str(category)] = [
                    t.get("topic_name", t) if isinstance(t, dict) else str(t) for t in topics
                ]

    # Normalized lookup variants
    normalized: Dict[str, List[str]] = {}
    for cat, themes in theme_map.items():
        normalized[cat] = themes
        cat_norm = (
            str(cat)
            .replace(" & ", "_and_")
            .replace("&", "and")
            .replace(" ", "_")
            .replace("-", "_")
            .lower()
        )
        normalized.setdefault(cat_norm, themes)
    return normalized


def _map_to_main_category(category: str, category_mapping: Optional[Dict[str, Any]]) -> str:
    if not category_mapping or not category:
        return category
    if isinstance(category_mapping, dict) and "category_to_main_mapping" in category_mapping:
        return str(category_mapping["category_to_main_mapping"].get(category, category))
    # direct mapping dict
    return str(category_mapping.get(category, category))


def identify_excluded_reviews(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    i0 pre-filter: exclude reviews with empty actual themes or empty predicted themes.
    Returns list of failure records (review_key, reason, etc).
    """
    failures: List[Dict[str, Any]] = []
    user_predictions = data.get("user_predictions", {})
    if not isinstance(user_predictions, dict):
        return failures

    for user_id, user_reviews in user_predictions.items():
        if not isinstance(user_reviews, list):
            continue
        for review_idx, review_entry in enumerate(user_reviews):
            review_key = f"{user_id}_review_{review_idx}"
            actual = review_entry.get("actual", {}) if isinstance(review_entry, dict) else {}
            prediction = review_entry.get("prediction", {}) if isinstance(review_entry, dict) else {}

            actual_themes = (actual or {}).get("predicted_themes", [])
            if not actual_themes or (isinstance(actual_themes, list) and len(actual_themes) == 0):
                failures.append(
                    {
                        "review_key": review_key,
                        "user_id": user_id,
                        "review_idx": review_idx,
                        "reason": "empty_actual_themes",
                        "review_data": review_entry,
                    }
                )
                continue

            predicted_themes = (prediction or {}).get("predicted_themes", {})
            if not predicted_themes or (isinstance(predicted_themes, dict) and len(predicted_themes) == 0):
                failures.append(
                    {
                        "review_key": review_key,
                        "user_id": user_id,
                        "review_idx": review_idx,
                        "reason": "empty_predicted_themes",
                        "review_data": review_entry,
                    }
                )
                continue

    return failures


def compute_and_save_i0_deltas_for_micro_cluster(
    *,
    filepath: str,
    client: Any,
    category_mapping: Optional[Dict[str, Any]] = None,
    output_cluster_id: Optional[str] = None,
    output_micro_id: Optional[str] = None,
) -> Tuple[str, str]:
    """
    Computes initial (i0) deltas for a micro-cluster input file and persists them into
    `OUTPUT_DIRS['delta']/<cluster>/<micro>_all_reviews_deltas.json` plus exclusions in failures.

    Returns:
      (all_reviews_deltas_file, failure_file)
    """
    data = io_utils.load_json_file(filepath)
    if not data or "user_predictions" not in data:
        raise ValueError(f"Invalid micro cluster file (missing user_predictions): {filepath}")

    # Infer ids from path when not provided (matches existing pipeline conventions)
    cluster_id = output_cluster_id
    micro_id = output_micro_id
    if not cluster_id or not micro_id:
        rel = os.path.relpath(filepath, settings.PATHS["PREDICTION_INPUT_DIR"])
        parts = rel.split(os.sep)
        cluster_id = cluster_id or parts[0]
        base = os.path.basename(filepath)
        # e.g. micro_12_summary_...json
        import re

        m = re.search(r"micro_(\d+)", base)
        if not m:
            raise ValueError(f"Could not infer micro_id from filename: {base}")
        micro_id = micro_id or f"micro_{m.group(1)}"

    failures_dir = settings.PATHS["OUTPUT_DIRS"]["failures"]
    delta_dir = settings.PATHS["OUTPUT_DIRS"]["delta"]
    cache_dir = settings.PATHS["OUTPUT_DIRS"]["cache"]
    if not (failures_dir and delta_dir and cache_dir):
        raise ValueError("settings.PATHS['OUTPUT_DIRS'] is not initialized")

    failure_file = os.path.join(failures_dir, cluster_id, f"{micro_id}_failures.json")
    cache_file = os.path.join(cache_dir, cluster_id, f"{micro_id}_cache.json")

    all_reviews_deltas_file = os.path.join(delta_dir, cluster_id, f"{micro_id}_all_reviews_deltas.json")
    os.makedirs(os.path.dirname(all_reviews_deltas_file), exist_ok=True)

    # If already computed, keep as-is (idempotent)
    existing = io_utils.load_json_file(all_reviews_deltas_file, {})
    if isinstance(existing, dict) and "deltas" in existing and isinstance(existing["deltas"], list):
        return all_reviews_deltas_file, failure_file

    fixed_data = copy.deepcopy(data)

    # exclusions
    excluded = identify_excluded_reviews(fixed_data)
    if excluded:
        os.makedirs(os.path.dirname(failure_file), exist_ok=True)
        existing_failures = io_utils.load_json_file(failure_file, [])
        if not isinstance(existing_failures, list):
            existing_failures = []
        existing_keys = {
            f.get("review_key")
            for f in existing_failures
            if isinstance(f, dict) and f.get("review_key")
        }
        merged = existing_failures + [f for f in excluded if f["review_key"] not in existing_keys]
        io_utils.save_json_file(merged, failure_file)

    excluded_keys = {f["review_key"] for f in excluded if isinstance(f, dict) and "review_key" in f}

    # theme universe map (for logprobs/JSD support)
    theme_map_raw = io_utils.load_json_file(settings.PATHS["THEMES_FILE"], {})
    theme_map = _normalize_theme_map(theme_map_raw)

    embeddings_cache = io_utils.load_json_file(cache_file, {})
    if not isinstance(embeddings_cache, dict):
        embeddings_cache = {}

    all_reviews_deltas: List[Dict[str, Any]] = []

    for user_id, user_reviews in fixed_data.get("user_predictions", {}).items():
        if not isinstance(user_reviews, list):
            continue
        for review_idx, review_entry in enumerate(user_reviews):
            review_key = f"{user_id}_review_{review_idx}"
            if review_key in excluded_keys:
                continue
            if not isinstance(review_entry, dict):
                continue

            prediction = review_entry.get("prediction", {}) or {}
            actual = review_entry.get("actual", {}) or {}

            pred_text = (prediction.get("review_text") or "").strip()
            act_text = (actual.get("review_text") or "").strip()
            if not pred_text or not act_text:
                continue

            # embeddings
            pred_emb = embeddings.get_or_compute_embedding(
                pred_text, review_key, "pred_initial", client, embeddings_cache
            )
            act_emb = embeddings.get_or_compute_embedding(
                act_text, review_key, "actual", client, embeddings_cache
            )
            if not pred_emb or not act_emb:
                continue
            text_delta = calculate_text_delta(pred_emb, act_emb)

            category_themes_list = None
            if getattr(settings, "SCORING_MODE", "confidence") in (
                "logprobs",
                "logprobs-without-persona-context",
            ):
                cat = str(review_entry.get("category") or "")
                main_cat = _map_to_main_category(cat, category_mapping)
                category_themes_list = theme_map.get(main_cat, [])
                if not category_themes_list:
                    main_norm = (
                        main_cat.replace(" & ", "_and_")
                        .replace("&", "and")
                        .replace(" ", "_")
                        .replace("-", "_")
                        .lower()
                    )
                    category_themes_list = theme_map.get(main_norm, [])

            theme_delta = get_theme_delta(
                prediction.get("predicted_themes", {}),
                actual.get("predicted_themes", []),
                all_themes=category_themes_list,
            )
            overall_delta = (text_delta * settings.WEIGHT_TEXT_DELTA) + (
                theme_delta * settings.WEIGHT_THEME_DELTA
            )

            all_reviews_deltas.append(
                {
                    "review_key": review_key,
                    "user_id": user_id,
                    "review_idx": review_idx,
                    "text_delta": text_delta,
                    "theme_delta": theme_delta,
                    "theme_jsd": theme_delta
                    if getattr(settings, "SCORING_MODE", "confidence")
                    in ("logprobs", "logprobs-without-persona-context")
                    else None,
                    "overall_delta": overall_delta,
                    "prediction": {
                        "review_text": pred_text,
                        "predicted_themes": prediction.get("predicted_themes", {}),
                    },
                    "actual": {
                        "review_text": act_text,
                        "predicted_themes": actual.get("predicted_themes", []),
                    },
                    "category": review_entry.get("category"),
                    "product_description": review_entry.get("product_description", ""),
                }
            )

    io_utils.save_json_file(embeddings_cache, cache_file)
    io_utils.save_json_file(
        {
            "cluster_id": cluster_id,
            "micro_cluster_id": micro_id,
            "total_reviews": len(all_reviews_deltas),
            "calculation_timestamp": time.time(),
            "deltas": all_reviews_deltas,
        },
        all_reviews_deltas_file,
    )
    return all_reviews_deltas_file, failure_file

