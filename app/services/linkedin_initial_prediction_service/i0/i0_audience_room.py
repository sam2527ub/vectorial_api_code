"""
Audience-room LinkedIn **i0** (synthetic post + topic logprobs + deltas).

Lives entirely under ``app.services.linkedin_initial_prediction_service`` — no ``scripts/`` imports.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import time
from functools import partial
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from openai import AsyncOpenAI, OpenAI

from .embeddings import get_or_compute_embedding
from .i0_initial_prediction import run_i0_initial_prediction
from .i0_linkedin_context import (
    extract_individual_profile_for_i0,
    load_profile_json,
    resolve_group_traits_text,
)
from .theme_delta import get_theme_delta
from .topic_presence_logprobs import process_single_post
from .weighted_review_metrics import calculate_review_metrics, calculate_text_delta

_PACKAGE_DIR = Path(__file__).resolve().parent.parent
_PROMPTS_DIR = _PACKAGE_DIR / "prompts"

logger = logging.getLogger(__name__)


def build_i0_initial_prediction_linkedin_prompt(
    *,
    group_summary: str,
    group_traits_text: str,
    individual_ctx: str,
    stimulus: str,
    category: str,
) -> str:
    path = _PROMPTS_DIR / "i0_initial_prediction_linkedin.txt"
    if not path.is_file():
        raise FileNotFoundError(f"i0 initial prediction prompt missing: {path}")
    template = path.read_text(encoding="utf-8")
    return template.format(
        group_summary=(group_summary or "").strip() or "(none)",
        group_traits=(group_traits_text or "").strip() or "(none)",
        user_char_summary=(individual_ctx or "").strip() or "(none)",
        stimulus_description=(stimulus or "").strip() or "(none)",
        category=(category or "").strip() or "unknown",
    )


def _build_i0_topic_message_fn(max_text_chars: int) -> Callable[[str, str, str], Tuple[str, str]]:
    sys_p = _PROMPTS_DIR / "i0_topic_presence_linkedin_system.txt"
    usr_p = _PROMPTS_DIR / "i0_topic_presence_linkedin_user.txt"
    sys_default = "You are a precise topic classifier for LinkedIn posts. Answer only Yes or No."
    usr_fallback = (
        'Category: "{category}"\n\nTopic: "{topic}"\n\nPost:\n"""{post_body}"""\n\n'
        'Does this post discuss "{topic}"? Answer only Yes or No.'
    )
    sys_text = sys_default
    if sys_p.is_file():
        sys_text = sys_p.read_text(encoding="utf-8").strip() or sys_default
    usr_template = usr_p.read_text(encoding="utf-8") if usr_p.is_file() else usr_fallback

    def _fn(topic: str, category: str, post_text: str) -> Tuple[str, str]:
        body = (post_text or "")[:max_text_chars]
        user_msg = usr_template.format(topic=topic, category=category, post_body=body)
        return sys_text, user_msg

    return _fn


def load_category_themes_map(mapping_path: Path) -> Dict[str, List[str]]:
    if not mapping_path.is_file():
        logger.warning("Category mapping not found: %s (theme JSD union only)", mapping_path)
        return {}
    data = json.loads(mapping_path.read_text(encoding="utf-8"))
    out: Dict[str, List[str]] = {}
    for row in data.get("categories", []) or []:
        cat = row.get("category")
        topics = row.get("topics") or []
        if cat:
            out[str(cat)] = list(topics)
    return out


def category_themes_for_post(
    category: str,
    theme_map: Dict[str, List[str]],
) -> Optional[List[str]]:
    if not category or not theme_map:
        return None
    if category in theme_map:
        return theme_map[category]
    cat_n = (
        category.replace(" & ", "_and_")
        .replace("&", "and")
        .replace(" ", "_")
        .replace("-", "_")
        .lower()
    )
    for k, v in theme_map.items():
        if k.replace(" ", "_").lower() == cat_n:
            return v
    return None


def gt_theme_set(topic_probs: Dict[str, Any], threshold: float) -> Set[str]:
    out: Set[str] = set()
    if not isinstance(topic_probs, dict):
        return out
    for name, v in topic_probs.items():
        try:
            p = float(v)
        except (TypeError, ValueError):
            continue
        if p >= threshold:
            out.add(str(name))
    return out


def recall_at_k(pred: Dict[str, float], gt: Set[str], k: int) -> float:
    if not gt:
        return 1.0
    if not pred:
        return 0.0
    top = sorted(pred.keys(), key=lambda x: float(pred.get(x) or 0.0), reverse=True)[:k]
    hit = sum(1 for t in top if t in gt)
    return hit / len(gt)


def recall_metrics(pred: Dict[str, float], gt: Set[str]) -> Dict[str, Any]:
    k = len(gt) if gt else 0
    top_m = max(3, k) if k else 3
    r1 = recall_at_k(pred, gt, 1)
    r3 = recall_at_k(pred, gt, 3)
    r5 = recall_at_k(pred, gt, 5)
    rmax = recall_at_k(pred, gt, top_m)
    rmax_08 = recall_at_k(
        {t: p for t, p in pred.items() if float(p) >= 0.8},
        gt,
        top_m,
    )
    rmax_085 = recall_at_k(
        {t: p for t, p in pred.items() if float(p) >= 0.85},
        gt,
        top_m,
    )
    num_08 = sum(1 for p in pred.values() if float(p) >= 0.8)
    num_085 = sum(1 for p in pred.values() if float(p) >= 0.85)
    return {
        "recall@1": r1,
        "recall@3": r3,
        "recall@5": r5,
        "recall@max(3,k)": rmax,
        "recall@max(3,k)_threshold_0.8": rmax_08,
        "num_themes_above_0.8": num_08,
        "num_additional_themes_0.8": max(0, num_08 - len(gt)),
        "recall@max(3,k)_threshold_0.85": rmax_085,
        "num_themes_above_0.85": num_085,
        "num_additional_themes_0.85": max(0, num_085 - len(gt)),
        "num_actual_themes": len(gt),
    }


def merge_metrics(
    prediction: Dict[str, Any],
    actual: Dict[str, Any],
    category_themes_list: Optional[List[str]],
    gt_set: Set[str],
) -> Dict[str, Any]:
    base = calculate_review_metrics(prediction, actual, all_themes=category_themes_list) or {}
    rm = recall_metrics(prediction.get("predicted_themes") or {}, gt_set)
    out = dict(base)
    for k, v in rm.items():
        if k not in out:
            out[k] = v
    return out


def measure_deltas(
    review_key: str,
    pred_text: str,
    act_text: str,
    pred_topics: Dict[str, float],
    gt_topic_probs: Dict[str, float],
    category_themes_list: Optional[List[str]],
    sync_client: OpenAI,
    emb_cache: Dict[str, Any],
    emb_lock: Lock,
    weight_text: float,
    weight_theme: float,
    iter_suffix: str,
    *,
    scoring_mode: str,
    embedding_model: str,
    embedding_min_interval_sec: float,
) -> Tuple[float, float, float]:
    text_delta: Optional[float] = None
    last_err: Optional[str] = None
    for retry in range(3):
        try:
            # emb_lock is cache-only inside get_or_compute_embedding — API calls run concurrently
            # across posts (measure_deltas is invoked via asyncio.to_thread per post).
            pred_emb = get_or_compute_embedding(
                pred_text,
                review_key,
                f"pred_{iter_suffix}",
                sync_client,
                emb_cache,
                embedding_model=embedding_model,
                min_interval_sec=embedding_min_interval_sec,
                lock=emb_lock,
            )
            act_emb = get_or_compute_embedding(
                act_text,
                review_key,
                "actual",
                sync_client,
                emb_cache,
                embedding_model=embedding_model,
                min_interval_sec=embedding_min_interval_sec,
                lock=emb_lock,
            )
            if pred_emb and act_emb:
                text_delta = float(calculate_text_delta(pred_emb, act_emb))
                break
            last_err = f"pred_emb={pred_emb is not None}, act_emb={act_emb is not None}"
        except Exception as e:
            last_err = str(e)
        if retry < 2:
            time.sleep(1.0)
    if text_delta is None:
        logger.warning(
            "[EMB] review_key=%s suffix=%s failed text_delta (%s); using 1.0",
            review_key,
            iter_suffix,
            last_err,
        )
        text_delta = 1.0

    theme_delta = float(
        get_theme_delta(
            pred_topics,
            gt_topic_probs,
            category_themes_list,
            scoring_mode=scoring_mode,
        )
    )
    overall_delta = float(weight_text * text_delta + weight_theme * theme_delta)
    return text_delta, theme_delta, overall_delta


async def llm_json(
    client: AsyncOpenAI,
    model: str,
    system: str,
    user: str,
    sem: asyncio.Semaphore,
    *,
    max_tokens: int = 2500,
) -> Dict[str, Any]:
    async with sem:
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            temperature=0.35,
            max_tokens=max_tokens,
            timeout=120.0,
        )
    raw = (resp.choices[0].message.content or "").strip()
    return json.loads(raw)


async def generate_initial_post(
    client: AsyncOpenAI,
    model: str,
    sem: asyncio.Semaphore,
    i0_user_prompt: str,
) -> str:
    system = (
        "You follow the user's instructions exactly. Output a single JSON object with key "
        '"post_text" (preferred) or "review_text" — string body of the LinkedIn post only, '
        "no markdown fence, no preamble."
    )
    data = await llm_json(client, model, system, i0_user_prompt, sem)
    text = (data.get("review_text") or data.get("post_text") or "").strip()
    return text


async def topics_on_body(
    client: AsyncOpenAI,
    model: str,
    sem: asyncio.Semaphore,
    base_post: Dict[str, Any],
    synthetic_body: str,
    topics: List[str],
    prob_threshold: float,
    max_retries: int,
    retry_delay: float,
    max_text_chars: int,
    *,
    use_i0_topic_prompts: bool = True,
) -> Dict[str, Any]:
    post = copy.deepcopy(base_post)
    post["body_text"] = synthetic_body
    topic_fn: Optional[Callable[[str, str, str], Tuple[str, str]]] = None
    if use_i0_topic_prompts:
        topic_fn = _build_i0_topic_message_fn(max_text_chars)
    return await process_single_post(
        client=client,
        model=model,
        post=post,
        topics=topics,
        semaphore=sem,
        prob_threshold=prob_threshold,
        max_retries=max_retries,
        retry_delay=retry_delay,
        max_text_chars=max_text_chars,
        include_post_body=True,
        topic_message_fn=topic_fn,
    )


def prediction_dict(text: str, topic_probs: Dict[str, float]) -> Dict[str, Any]:
    return {
        "review_text": text,
        "predicted_themes": topic_probs,
    }


def append_all_reviews_delta(
    rows: List[Dict[str, Any]],
    *,
    review_key: str,
    user_id: str,
    review_idx: int,
    post_id: str,
    text_delta: float,
    theme_delta: float,
    overall_delta: float,
    prediction: Dict[str, Any],
    actual: Dict[str, Any],
    category: str,
    product_description: str,
    iter_label: str,
    scoring_mode: str,
) -> None:
    scoring = (scoring_mode or "confidence").strip().lower()
    rows.append(
        {
            "review_key": review_key,
            "user_id": user_id,
            "review_idx": review_idx,
            "post_id": post_id,
            "iteration_label": iter_label,
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


async def run_audience_room_i0_for_post(
    *,
    review_key: str,
    profile_id: str,
    review_idx: int,
    post: Dict[str, Any],
    client: AsyncOpenAI,
    sync_client: OpenAI,
    gen_model: str,
    topic_model: str,
    sem: asyncio.Semaphore,
    profiles_root: Path,
    group_summary: str,
    qualitative_summary: Dict[str, Any],
    emb_cache: Dict[str, Any],
    emb_lock: Lock,
    category_themes_list: Optional[List[str]],
    all_delta_rows: List[Dict[str, Any]],
    delta_rows_lock: Optional[asyncio.Lock],
    prob_threshold: float,
    max_retries: int,
    retry_delay: float,
    max_text_chars: int,
    delta_rows_iter_prefix: str,
    description_json_path: Optional[Path],
    debug_i0_prompt: bool,
    gt_prob_threshold: float,
    weight_text: float,
    weight_theme: float,
    print_i0_prompt: bool = False,
    prompt_print_lock: Optional[asyncio.Lock] = None,
    scoring_mode: str = "logprobs",
    embedding_model: str = "text-embedding-3-small",
    embedding_min_interval_sec: float = 0.05,
) -> Dict[str, Any]:
    """Single-post i0: synthetic post + topic logprobs + deltas."""
    post_id = str(post.get("post_id"))
    topics = list(post.get("topics_ordered") or [])
    if not topics:
        raise ValueError(f"post {post_id} missing topics_ordered")

    card = load_profile_json(profiles_root, profile_id)
    individual_ctx = extract_individual_profile_for_i0(card, profile_id=profile_id)
    group_traits_text = resolve_group_traits_text(description_json_path, qualitative_summary)
    stim = (post.get("stimulus") or "").strip()
    category = str(post.get("category") or "")
    gt_probs = {k: float(v) for k, v in (post.get("topic_probabilities") or {}).items()}
    theme_jsd_support = category_themes_list if category_themes_list else topics

    i0_user_prompt = build_i0_initial_prediction_linkedin_prompt(
        group_summary=group_summary,
        group_traits_text=group_traits_text,
        individual_ctx=individual_ctx,
        stimulus=stim,
        category=category,
    )
    prof_path = (profiles_root / profile_id / "profile.json").resolve()
    if debug_i0_prompt:
        banner = "=" * 72
        logger.info(
            "\n%s\n[I0 DEBUG] review_key=%s profile_id=%s\nprofile.json path=%s\n%s\n%s\n%s\n%s\n%s\n",
            banner,
            review_key,
            profile_id,
            prof_path,
            banner,
            "[I0 DEBUG] USER PROFILE (individual context)\n" + individual_ctx,
            banner,
            "[I0 DEBUG] FULL I0 USER PROMPT\n" + i0_user_prompt,
            banner,
        )

    if print_i0_prompt and prompt_print_lock is not None:
        banner = "=" * 72
        block = (
            f"\n{banner}\n[I0 PROMPT] review_key={review_key} profile_id={profile_id}\n"
            f"profile.json: {prof_path}\n{banner}\n"
            f"[GROUP_TRAITS_TEXT]\n{group_traits_text}\n{banner}\n"
            f"[STIMULUS] {stim!r}\n[CATEGORY] {category!r}\n{banner}\n"
            f"[INDIVIDUAL CONTEXT — profile excerpt for i0]\n{individual_ctx}\n{banner}\n"
            f"[FULL I0 USER PROMPT — sent to generation model]\n{i0_user_prompt}\n{banner}\n"
        )
        async with prompt_print_lock:
            print(block, flush=True)

    measure_fn = partial(
        measure_deltas,
        scoring_mode=scoring_mode,
        embedding_model=embedding_model,
        embedding_min_interval_sec=embedding_min_interval_sec,
    )
    append_fn = partial(append_all_reviews_delta, scoring_mode=scoring_mode)

    entry = await run_i0_initial_prediction(
        review_key=review_key,
        profile_id=profile_id,
        review_idx=review_idx,
        post=post,
        i0_user_prompt=i0_user_prompt,
        gt_probs=gt_probs,
        theme_jsd_support=theme_jsd_support,
        gt_prob_threshold=gt_prob_threshold,
        client=client,
        sync_client=sync_client,
        gen_model=gen_model,
        topic_model=topic_model,
        sem=sem,
        emb_cache=emb_cache,
        emb_lock=emb_lock,
        weight_text=weight_text,
        weight_theme=weight_theme,
        prob_threshold=prob_threshold,
        max_retries=max_retries,
        retry_delay=retry_delay,
        max_text_chars=max_text_chars,
        all_delta_rows=all_delta_rows,
        delta_rows_lock=delta_rows_lock,
        delta_rows_iter_prefix=delta_rows_iter_prefix,
        generate_initial_post=generate_initial_post,
        topics_on_body=topics_on_body,
        measure_deltas=measure_fn,
        prediction_dict=prediction_dict,
        merge_metrics=merge_metrics,
        gt_theme_set=gt_theme_set,
        append_all_reviews_delta=append_fn,
        scoring_mode=str(scoring_mode or "logprobs"),
    )
    entry["profile_json_path"] = str(prof_path)
    entry["i0_individual_profile_context"] = individual_ctx
    return entry
