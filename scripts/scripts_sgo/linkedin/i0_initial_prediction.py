from __future__ import annotations

import copy
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple


AsyncGenerateInitialPost = Callable[
    [Any, str, Any, str],
    Awaitable[str],
]
AsyncTopicsOnBody = Callable[
    [Any, str, Any, Dict[str, Any], str, List[str], float, int, float, int],
    Awaitable[Dict[str, Any]],
]
MeasureDeltasFn = Callable[
    [str, str, str, Dict[str, float], Dict[str, float], List[str], Any, Dict[str, Any], Any, float, float, str],
    Tuple[float, float, float],
]
PredictionDictFn = Callable[[str, Dict[str, float]], Dict[str, Any]]
MergeMetricsFn = Callable[[Dict[str, Any], Dict[str, Any], List[str], Any], Dict[str, Any]]
GtThemeSetFn = Callable[[Dict[str, float], float], Any]
AppendAllReviewsDeltaFn = Callable[..., None]


async def run_i0_initial_prediction(
    *,
    review_key: str,
    profile_id: str,
    review_idx: int,
    post: Dict[str, Any],
    i0_user_prompt: str,
    gt_probs: Dict[str, float],
    theme_jsd_support: List[str],
    gt_prob_threshold: float,
    client: Any,
    sync_client: Any,
    gen_model: str,
    topic_model: str,
    sem: Any,
    emb_cache: Dict[str, Any],
    emb_lock: Any,
    weight_text: float,
    weight_theme: float,
    prob_threshold: float,
    max_retries: int,
    retry_delay: float,
    max_text_chars: int,
    all_delta_rows: List[Dict[str, Any]],
    delta_rows_lock: Optional[Any],
    delta_rows_iter_prefix: str,
    generate_initial_post: AsyncGenerateInitialPost,
    topics_on_body: AsyncTopicsOnBody,
    measure_deltas: MeasureDeltasFn,
    prediction_dict: PredictionDictFn,
    merge_metrics: MergeMetricsFn,
    gt_theme_set: GtThemeSetFn,
    append_all_reviews_delta: AppendAllReviewsDeltaFn,
    scoring_mode: str,
) -> Dict[str, Any]:
    """
    Phase i0: generate initial synthetic post + predicted topics + compute deltas, and
    return the canonical "entry" dict used by the delta-method pipeline.
    """
    post_id = str(post.get("post_id"))
    topics = list(post.get("topics_ordered") or [])
    stim = (post.get("stimulus") or "").strip()
    body = (post.get("body_text") or "").strip()
    category = str(post.get("category") or "")

    gt_set = gt_theme_set(gt_probs, gt_prob_threshold)

    synthetic = await generate_initial_post(client, gen_model, sem, i0_user_prompt)
    t_out = await topics_on_body(
        client,
        topic_model,
        sem,
        post,
        synthetic,
        topics,
        prob_threshold,
        max_retries,
        retry_delay,
        max_text_chars,
    )
    pred_topics = {k: float(v) for k, v in (t_out.get("topic_probabilities") or {}).items()}

    td, thm, ov = await __import__("asyncio").to_thread(
        measure_deltas,
        review_key,
        synthetic,
        body,
        pred_topics,
        gt_probs,
        theme_jsd_support,
        sync_client,
        emb_cache,
        emb_lock,
        weight_text,
        weight_theme,
        "initial",
    )

    pred_block = prediction_dict(synthetic, pred_topics)
    actual_block = {
        "review_text": body,
        "predicted_themes": gt_probs,
        "topic_probabilities": gt_probs,
    }
    metrics = merge_metrics(pred_block, actual_block, theme_jsd_support, gt_set)

    i0_topic_classification = {
        "topics_ordered": t_out.get("topics_ordered"),
        "topic_probabilities": pred_topics,
        "topic_probabilities_before_normalisation": t_out.get(
            "topic_probabilities_before_normalisation"
        ),
        "topic_logprobs": t_out.get("topic_logprobs"),
        "per_topic_errors": t_out.get("per_topic_errors"),
        "classification_method": t_out.get("classification_method"),
        "prediction_uses_post_body": t_out.get("prediction_uses_post_body"),
        "predicted_theme_labels": t_out.get("predicted_themes"),
    }

    initial_prediction_deltas = {
        "text_delta": td,
        "theme_delta": thm,
        "overall_delta": ov,
    }
    deltas_block = {
        "text_delta": td,
        "theme_delta": thm,
        "overall_delta": ov,
        "theme_jsd": thm if scoring_mode in ("logprobs", "logprobs-without-persona-context") else None,
        "delta_weights": {"text": weight_text, "theme": weight_theme},
    }

    iter_label = f"{delta_rows_iter_prefix}initial" if delta_rows_iter_prefix else "initial"
    if delta_rows_lock is not None:
        async with delta_rows_lock:
            append_all_reviews_delta(
                all_delta_rows,
                review_key=review_key,
                user_id=profile_id,
                review_idx=review_idx,
                post_id=post_id,
                text_delta=td,
                theme_delta=thm,
                overall_delta=ov,
                prediction=pred_block,
                actual=actual_block,
                category=category,
                product_description=stim,
                iter_label=iter_label,
            )
    else:
        append_all_reviews_delta(
            all_delta_rows,
            review_key=review_key,
            user_id=profile_id,
            review_idx=review_idx,
            post_id=post_id,
            text_delta=td,
            theme_delta=thm,
            overall_delta=ov,
            prediction=pred_block,
            actual=actual_block,
            category=category,
            product_description=stim,
            iter_label=iter_label,
        )

    entry: Dict[str, Any] = {
        "review_key": review_key,
        "user_id": profile_id,
        "review_idx": review_idx,
        "status": "success",
        "product_description": stim,
        "category": category,
        "post_id": post_id,
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
            "topic_classification": copy.deepcopy(i0_topic_classification),
        },
        "i0_topic_classification": copy.deepcopy(i0_topic_classification),
        "correction_journey": [],
    }

    return entry
