"""Run per-topic ground-truth labels (LLM Yes/No + logprobs) over contextual stimulus JSON."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from openai import AsyncOpenAI

from .errors import GroundTruthExtractionError
from .ground_truth_extraction_config import (
    GroundTruthExtractionConfig,
    get_ground_truth_extraction_config,
    load_ground_truth_extraction_config,
)
from .ground_truth_extraction_core import (
    classification_done,
    has_successful_topic_labels,
    iter_mutable_posts,
    mark_missing_body_on_post,
    mark_unknown_category_on_post,
    merge_classification_into_post,
    normalize_contextual_payload,
    softmax_over_logprob_yes,
    topics_mapping_from_dict,
)
from .ground_truth_extraction_llm import get_topic_logprobs

log = logging.getLogger(__name__)


def _new_openai_client(cfg: GroundTruthExtractionConfig) -> AsyncOpenAI:
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key.strip():
        raise GroundTruthExtractionError("OPENAI_API_KEY is not set")
    base = os.environ.get("OPENAI_BASE_URL", "").strip()
    return AsyncOpenAI(
        api_key=key,
        base_url=base if base else None,
        timeout=cfg.client_total_timeout_sec,
    )


async def process_single_post(
    client: AsyncOpenAI,
    model: str,
    post: Dict[str, Any],
    topics: List[str],
    llm_semaphore: asyncio.Semaphore,
    prob_threshold: float,
    max_text_chars: int,
    per_call_timeout: float,
    retry_cfg: Any,
    *,
    include_post_body: bool = True,
) -> Dict[str, Any]:
    from .ground_truth_extraction_core import logprob_fallback

    _LP_FALLBACK = logprob_fallback()
    text = (post.get("body_text") or "") if include_post_body else ""
    cat = post.get("category") or ""

    async def _one(topic: str) -> Any:
        return await get_topic_logprobs(
            client,
            model,
            str(text),
            str(topic),
            str(cat),
            llm_semaphore,
            per_call_timeout,
            retry_cfg,
            max_text_chars,
            include_post_body=include_post_body,
        )

    # All topics for this post in parallel; shared llm_semaphore caps in-flight API calls globally.
    topic_results: List[Any] = list(
        await asyncio.gather(*(_one(t) for t in topics), return_exceptions=True)
    )

    before_norm: Dict[str, float] = {}
    topic_logprobs: Dict[str, Dict[str, float]] = {}
    predicted_themes: List[str] = []
    per_topic_errors: Dict[str, str] = {}

    for topic, result in zip(topics, topic_results):
        if isinstance(result, Exception):
            log.warning("topic %r: %s", topic[:50], result)
            before_norm[topic] = 0.0
            topic_logprobs[topic] = {"logprob_yes": _LP_FALLBACK, "logprob_no": _LP_FALLBACK}
            per_topic_errors[topic] = str(result)
            continue

        if result.get("error") and result["error"] not in (
            None,
            "logprobs_unavailable_fallback",
            "model_without_logprobs_fallback",
        ):
            per_topic_errors[topic] = str(result["error"])

        py = float(result.get("yes", 0.0))
        before_norm[topic] = py
        topic_logprobs[topic] = {
            "logprob_yes": float(result.get("logprob_yes", _LP_FALLBACK)),
            "logprob_no": float(result.get("logprob_no", _LP_FALLBACK)),
        }
        if py > prob_threshold:
            predicted_themes.append(topic)

    topic_probabilities = softmax_over_logprob_yes(topics, topic_logprobs)

    return {
        "topics_ordered": topics,
        "topic_probabilities": topic_probabilities,
        "topic_probabilities_before_normalisation": before_norm,
        "topic_logprobs": topic_logprobs,
        "predicted_themes": predicted_themes,
        "num_topics_classified": len(topics),
        "per_topic_errors": per_topic_errors,
        "classification_method": "per_topic_yes_no_logprobs",
        "prediction_uses_post_body": include_post_body,
    }


async def _classify_one_post(
    *,
    post: Dict[str, Any],
    client: AsyncOpenAI,
    model: str,
    topics_by_category: Dict[str, List[str]],
    llm_sem: asyncio.Semaphore,
    post_sem: asyncio.Semaphore,
    cfg: GroundTruthExtractionConfig,
    is_tier2: bool,
) -> None:
    async with post_sem:
        cat = (post.get("category") or "").strip()
        topics = topics_by_category.get(cat) or []
        if not topics:
            mark_unknown_category_on_post(post)
            return
        if not (post.get("body_text") or "").strip():
            mark_missing_body_on_post(post)
            return
        retry = cfg.tier2 if is_tier2 else cfg.tier1
        out = await process_single_post(
            client=client,
            model=model,
            post=post,
            topics=topics,
            llm_semaphore=llm_sem,
            prob_threshold=cfg.prob_threshold,
            max_text_chars=cfg.max_text_chars,
            per_call_timeout=cfg.per_call_timeout_sec,
            retry_cfg=retry,
            include_post_body=cfg.include_post_body,
        )
        merge_classification_into_post(post, out)


def _config_with_overrides(
    base: GroundTruthExtractionConfig,
    max_concurrent_llm: Optional[int],
    max_concurrent_posts: Optional[int],
) -> GroundTruthExtractionConfig:
    if max_concurrent_llm is None and max_concurrent_posts is None:
        return base
    return replace(
        base,
        max_concurrent_llm=int(max_concurrent_llm)
        if max_concurrent_llm is not None
        else base.max_concurrent_llm,
        max_concurrent_posts=int(max_concurrent_posts)
        if max_concurrent_posts is not None
        else base.max_concurrent_posts,
    )


async def run_ground_truth_extraction_on_payload(
    payload: Dict[str, Any],
    topics_by_category: Dict[str, List[str]],
    model: str,
    *,
    is_tier2: bool,
    config: Optional[GroundTruthExtractionConfig] = None,
    max_concurrent_llm: Optional[int] = None,
    max_concurrent_posts: Optional[int] = None,
    on_after_terminal_marks: Optional[Callable[[Dict[str, Any]], None]] = None,
    max_posts_to_classify: Optional[int] = None,
) -> Dict[str, Any]:
    """
    In-place: fill per-topic ground-truth fields on posts that are not already classified.
    Within each post, topic calls run **in parallel** (``asyncio.gather``), bounded by the shared
    ``llm`` semaphore. Multiple posts are classified **in parallel** as well, bounded by
    ``max_concurrent_posts``. Set ``max_concurrent_llm`` well above ``max_concurrent_posts``
    (e.g. posts × avg topics per category) so topic parallelism is not starved.
    """
    raw = config or load_ground_truth_extraction_config()
    cfg = _config_with_overrides(raw, max_concurrent_llm, max_concurrent_posts)
    working = normalize_contextual_payload(payload)
    mdl = model or os.environ.get("OPENAI_MODEL", cfg.default_model)

    all_refs = iter_mutable_posts(working)
    pending: List[Dict[str, Any]] = []
    skipped_no_body = 0
    marked_unknown_scan = 0
    for _uid, post in all_refs:
        if classification_done(post):
            continue
        body = (post.get("body_text") or "").strip()
        if not body:
            skipped_no_body += 1
            mark_missing_body_on_post(post)
            continue
        cat = (post.get("category") or "").strip()
        tlist = topics_by_category.get(cat) or []
        if not tlist:
            mark_unknown_category_on_post(post)
            marked_unknown_scan += 1
            continue
        pending.append(post)

    if max_posts_to_classify is not None and max_posts_to_classify > 0:
        pending = pending[: int(max_posts_to_classify)]

    if on_after_terminal_marks is not None and (skipped_no_body + marked_unknown_scan) > 0:
        on_after_terminal_marks(working)

    total_posts = len(all_refs)
    if pending:
        client = _new_openai_client(cfg)
        llm_sem = asyncio.Semaphore(max(1, cfg.max_concurrent_llm))
        post_sem = asyncio.Semaphore(max(1, cfg.max_concurrent_posts))
        await asyncio.gather(
            *(
                _classify_one_post(
                    post=p,
                    client=client,
                    model=mdl,
                    topics_by_category=topics_by_category,
                    llm_sem=llm_sem,
                    post_sem=post_sem,
                    cfg=cfg,
                    is_tier2=is_tier2,
                )
                for p in pending
            )
        )

    done_success = sum(1 for _, p in all_refs if has_successful_topic_labels(p))
    meta: Dict[str, Any] = {
        "model": mdl,
        "tier": "tier2" if is_tier2 else "tier1",
        "prob_threshold": cfg.prob_threshold,
        "classification_method": "per_topic_yes_no_logprobs",
        "topic_llm_scheduling": "parallel_topics_parallel_posts",
        "concurrency": {
            "max_concurrent_llm": cfg.max_concurrent_llm,
            "max_concurrent_posts": cfg.max_concurrent_posts,
        },
        "counts": {
            "total_posts": total_posts,
            "pending_llm": len(pending),
            "terminal_empty_body": skipped_no_body,
            "terminal_unknown_category": marked_unknown_scan,
            "with_successful_topic_labels": done_success,
        },
        "schema_note": (
            "topic_probabilities = softmax(logprob_yes) over category topics; "
            "topic_probabilities_before_normalisation = P_yes after each call's yes/no normalize"
        ),
        "run_at": datetime.now(timezone.utc).isoformat(),
    }
    working["ground_truth_extraction_meta"] = meta
    # Back-compat: some notebooks expect this key
    working["topic_classification_meta"] = meta
    return meta


def topics_mapping_from_mapping_object(data: Dict[str, Any]) -> Dict[str, List[str]]:
    return topics_mapping_from_dict(data)


# Back-compat
enrich_contextual_stimulus_with_topic_presence = run_ground_truth_extraction_on_payload


def collect_pending_ground_truth_posts(
    working: Dict[str, Any], topics_by_category: Dict[str, List[str]]
) -> List[Dict[str, Any]]:
    """Ordered list of posts that still need per-topic ground-truth labels (LLM path)."""
    out: List[Dict[str, Any]] = []
    w = normalize_contextual_payload(working)
    for _uid, post in iter_mutable_posts(w):
        if classification_done(post):
            continue
        body = (post.get("body_text") or "").strip()
        if not body:
            continue
        cat = (post.get("category") or "").strip()
        if not topics_by_category.get(cat):
            continue
        out.append(post)
    return out


async def run_ground_truth_extraction_on_post_list(
    working: Dict[str, Any],
    topics_by_category: Dict[str, List[str]],
    post_list: List[Dict[str, Any]],
    model: str,
    *,
    is_tier2: bool,
    config: Optional[GroundTruthExtractionConfig] = None,
) -> None:
    """In-place: classify only the given post dicts (same references as in ``working``)."""
    if not post_list:
        return
    raw = config or load_ground_truth_extraction_config()
    cfg = raw
    mdl = model or os.environ.get("OPENAI_MODEL", cfg.default_model)
    client = _new_openai_client(cfg)
    llm_sem = asyncio.Semaphore(max(1, cfg.max_concurrent_llm))
    post_sem = asyncio.Semaphore(max(1, cfg.max_concurrent_posts))
    await asyncio.gather(
        *(
            _classify_one_post(
                post=p,
                client=client,
                model=mdl,
                topics_by_category=topics_by_category,
                llm_sem=llm_sem,
                post_sem=post_sem,
                cfg=cfg,
                is_tier2=is_tier2,
            )
            for p in post_list
        )
    )


__all__ = (
    "run_ground_truth_extraction_on_payload",
    "enrich_contextual_stimulus_with_topic_presence",
    "run_ground_truth_extraction_on_post_list",
    "collect_pending_ground_truth_posts",
    "process_single_post",
    "topics_mapping_from_mapping_object",
)
