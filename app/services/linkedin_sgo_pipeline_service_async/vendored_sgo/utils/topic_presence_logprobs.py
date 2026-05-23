"""
Per-topic Yes/No classification with token logprobs for LinkedIn-style posts.

Used by ``linkedin/tier1_delta_method_predictions`` and related SGO flows. Implements the same
contract as the legacy ``contextual_post_topic_presence`` pipeline: parallel calls per topic,
softmax over ``logprob_yes`` for ``topic_probabilities``.
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any, Callable, Dict, List, Optional, Tuple

from openai import AsyncOpenAI

from utils.openai_chat_params import build_chat_completion_kwargs, model_supports_logprobs

logger = logging.getLogger(__name__)

_LP_FALLBACK = -10.0


def softmax_over_logprob_yes(
    topics: List[str],
    topic_logprobs: Dict[str, Dict[str, float]],
) -> Dict[str, float]:
    """Numerically stable softmax on ``logprob_yes``; values sum to 1 over ``topics`` order."""
    if not topics:
        return {}
    lps: List[float] = []
    for t in topics:
        row = topic_logprobs.get(t) or {}
        v = float(row.get("logprob_yes", _LP_FALLBACK))
        lps.append(v if math.isfinite(v) else _LP_FALLBACK)
    m = max(lps)
    exps = [math.exp(lp - m) for lp in lps]
    s = sum(exps)
    if s <= 0 or not math.isfinite(s):
        u = 1.0 / len(topics)
        return {t: u for t in topics}
    return {t: e / s for t, e in zip(topics, exps)}


def _yes_no_logprobs_from_completion(logprobs_data: Any) -> Tuple[float, float, Optional[str], Optional[str]]:
    yes_lp = float("-inf")
    no_lp = float("-inf")
    yes_token: Optional[str] = None
    no_token: Optional[str] = None

    if not logprobs_data or not hasattr(logprobs_data, "content"):
        return _LP_FALLBACK, _LP_FALLBACK, None, None

    def _lp(val: Any) -> float:
        if val is None:
            return float("-inf")
        try:
            x = float(val)
            return x if math.isfinite(x) else float("-inf")
        except (TypeError, ValueError):
            return float("-inf")

    try:
        for token_info in logprobs_data.content:
            lp = _lp(getattr(token_info, "logprob", None))
            tok = getattr(token_info, "token", "") or ""
            clean = tok.lower().strip().strip('"').strip("'")
            if clean in ("yes", "y"):
                if lp > yes_lp:
                    yes_lp = lp
                    yes_token = tok
            elif clean in ("no", "n"):
                if lp > no_lp:
                    no_lp = lp
                    no_token = tok

            alts = getattr(token_info, "top_logprobs", None) or []
            for alt in alts:
                alt_tok = getattr(alt, "token", "") or ""
                ac = alt_tok.lower().strip().strip('"').strip("'")
                alt_lp = _lp(getattr(alt, "logprob", None))
                if ac in ("yes", "y"):
                    if alt_lp > yes_lp:
                        yes_lp = alt_lp
                        yes_token = alt_tok
                elif ac in ("no", "n"):
                    if alt_lp > no_lp:
                        no_lp = alt_lp
                        no_token = alt_tok
    except Exception as e:
        logger.error("logprob parse error: %s", e)
        return _LP_FALLBACK, _LP_FALLBACK, None, None

    if not math.isfinite(yes_lp):
        yes_lp = _LP_FALLBACK
    if not math.isfinite(no_lp):
        no_lp = _LP_FALLBACK

    return yes_lp, no_lp, yes_token, no_token


def _normalize_yes_no_probs(logprob_yes: float, logprob_no: float) -> Tuple[float, float]:
    ly = float(logprob_yes) if math.isfinite(logprob_yes) else _LP_FALLBACK
    ln = float(logprob_no) if math.isfinite(logprob_no) else _LP_FALLBACK
    py = math.exp(ly)
    pn = math.exp(ln)
    total = py + pn
    if total > 0 and math.isfinite(total):
        return py / total, pn / total
    return 0.5, 0.5


async def get_topic_logprobs(
    client: AsyncOpenAI,
    model: str,
    post_text: str,
    topic: str,
    category: str,
    semaphore: asyncio.Semaphore,
    max_retries: int,
    retry_delay: float,
    max_text_chars: int,
    *,
    include_post_body: bool = True,
    system_override: Optional[str] = None,
    user_override: Optional[str] = None,
) -> Dict[str, Any]:
    if system_override is not None and user_override is not None:
        system_msg = system_override
        user_msg = user_override
    elif include_post_body:
        system_msg = "You are a precise topic classifier for professional social posts. Answer only Yes or No."
        body = (post_text or "")[:max_text_chars]
        user_msg = (
            f'Category context: "{category}"\n\n'
            f'Analyze this LinkedIn-style post and determine if it discusses the topic "{topic}".\n\n'
            f'Post:\n"""{body}"""\n\n'
            f'Does this post discuss the topic "{topic}"?\n'
            f'Answer with ONLY "Yes" or "No".'
        )
    else:
        system_msg = (
            "You estimate whether a LinkedIn post (which you do NOT see) would likely discuss a topic, "
            "given only its category. Answer only Yes or No."
        )
        user_msg = (
            f'Category: "{category}"\n\n'
            f'Question: Would a typical post in this category likely discuss the topic "{topic}"?\n'
            f'You do not have the post body. Answer with ONLY "Yes" or "No".'
        )

    last_err: Optional[Exception] = None
    for attempt in range(max_retries):
        async with semaphore:
            try:
                if model_supports_logprobs(model):
                    try:
                        response = await client.chat.completions.create(
                            **build_chat_completion_kwargs(
                                model,
                                [
                                    {"role": "system", "content": system_msg},
                                    {"role": "user", "content": user_msg},
                                ],
                                max_tokens=5,
                                temperature=0,
                                logprobs=True,
                                top_logprobs=5,
                                timeout=90.0,
                            )
                        )
                        ch = response.choices[0]
                        logprobs_data = getattr(ch, "logprobs", None)
                        yes_lp, no_lp, yes_tok, no_tok = _yes_no_logprobs_from_completion(logprobs_data)
                        prob_yes, prob_no = _normalize_yes_no_probs(yes_lp, no_lp)

                        return {
                            "yes": prob_yes,
                            "no": prob_no,
                            "logprob_yes": yes_lp,
                            "logprob_no": no_lp,
                            "token_yes": yes_tok,
                            "token_no": no_tok,
                            "error": None,
                        }
                    except Exception as logprob_error:
                        err_s = str(logprob_error).lower()
                        if "logprob" in err_s or "403" in err_s:
                            response = await client.chat.completions.create(
                                **build_chat_completion_kwargs(
                                    model,
                                    [
                                        {"role": "system", "content": system_msg},
                                        {"role": "user", "content": user_msg},
                                    ],
                                    max_tokens=5,
                                    temperature=0,
                                    timeout=90.0,
                                )
                            )
                            answer_text = (response.choices[0].message.content or "").strip().lower()
                            is_yes = "yes" in answer_text or answer_text.startswith("y")
                            prob_yes = 0.9 if is_yes else 0.1
                            prob_no = 0.1 if is_yes else 0.9
                            return {
                                "yes": prob_yes,
                                "no": prob_no,
                                "logprob_yes": math.log(prob_yes) if prob_yes > 0 else _LP_FALLBACK,
                                "logprob_no": math.log(prob_no) if prob_no > 0 else _LP_FALLBACK,
                                "token_yes": "Yes" if is_yes else None,
                                "token_no": "No" if not is_yes else None,
                                "error": "logprobs_unavailable_fallback",
                            }
                        raise
                else:
                    response = await client.chat.completions.create(
                        **build_chat_completion_kwargs(
                            model,
                            [
                                {"role": "system", "content": system_msg},
                                {"role": "user", "content": user_msg},
                            ],
                            max_tokens=5,
                            temperature=0,
                            timeout=90.0,
                        )
                    )
                    answer_text = (response.choices[0].message.content or "").strip().lower()
                    is_yes = "yes" in answer_text or answer_text.startswith("y")
                    prob_yes = 0.9 if is_yes else 0.1
                    prob_no = 0.1 if is_yes else 0.9
                    return {
                        "yes": prob_yes,
                        "no": prob_no,
                        "logprob_yes": math.log(prob_yes) if prob_yes > 0 else _LP_FALLBACK,
                        "logprob_no": math.log(prob_no) if prob_no > 0 else _LP_FALLBACK,
                        "token_yes": "Yes" if is_yes else None,
                        "token_no": "No" if not is_yes else None,
                        "error": "model_without_logprobs_fallback",
                    }
            except Exception as e:
                last_err = e
                logger.warning("get_topic_logprobs attempt %s topic=%r: %s", attempt + 1, topic[:40], e)
                if attempt == max_retries - 1:
                    return {
                        "yes": 0.0,
                        "no": 0.0,
                        "logprob_yes": _LP_FALLBACK,
                        "logprob_no": _LP_FALLBACK,
                        "token_yes": None,
                        "token_no": None,
                        "error": str(e),
                    }
                await asyncio.sleep(retry_delay * (attempt + 1))

    return {
        "yes": 0.0,
        "no": 0.0,
        "logprob_yes": _LP_FALLBACK,
        "logprob_no": _LP_FALLBACK,
        "token_yes": None,
        "token_no": None,
        "error": str(last_err) if last_err else "unexpected",
    }


async def process_single_post(
    client: AsyncOpenAI,
    model: str,
    post: Dict[str, Any],
    topics: List[str],
    semaphore: asyncio.Semaphore,
    prob_threshold: float,
    max_retries: int,
    retry_delay: float,
    max_text_chars: int,
    *,
    include_post_body: bool = True,
    topic_message_fn: Optional[Callable[[str, str, str], Tuple[str, str]]] = None,
) -> Dict[str, Any]:
    text = (post.get("body_text") or "") if include_post_body else ""
    cat = post["category"]
    tasks = []
    for topic in topics:
        so: Optional[str] = None
        uo: Optional[str] = None
        if topic_message_fn is not None and include_post_body:
            so, uo = topic_message_fn(str(topic), str(cat), text)
        tasks.append(
            get_topic_logprobs(
                client,
                model,
                text,
                topic,
                cat,
                semaphore,
                max_retries=max_retries,
                retry_delay=retry_delay,
                max_text_chars=max_text_chars,
                include_post_body=include_post_body,
                system_override=so,
                user_override=uo,
            )
        )
    topic_results = await asyncio.gather(*tasks, return_exceptions=True)

    before_norm: Dict[str, float] = {}
    topic_logprobs: Dict[str, Dict[str, float]] = {}
    predicted_themes: List[str] = []
    per_topic_errors: Dict[str, str] = {}

    for topic, result in zip(topics, topic_results):
        if isinstance(result, Exception):
            logger.warning("topic %r: %s", topic[:50], result)
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
