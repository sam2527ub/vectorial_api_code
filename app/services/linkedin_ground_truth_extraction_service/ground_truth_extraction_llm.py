"""OpenAI async chat (Yes/No + logprobs) with transient-error retries."""

from __future__ import annotations

import asyncio
import logging
import math
import random
from typing import Any, Dict, Optional

from openai import APIConnectionError, APIError, APITimeoutError, AsyncOpenAI, RateLimitError

from .ground_truth_extraction_config import GroundTruthTierRetry
from .ground_truth_extraction_core import (
    logprob_fallback,
    normalize_yes_no_probs,
    yes_no_logprobs_from_completion,
)

log = logging.getLogger(__name__)
_LP_FALLBACK = logprob_fallback()


def model_supports_logprobs(model_name: str) -> bool:
    m = model_name.lower()
    return any(x in m for x in ("gpt-4", "gpt-3.5", "gpt-4o", "gpt-4-turbo", "gpt-oss", "oss"))


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (RateLimitError, APIConnectionError, APITimeoutError, TimeoutError)):
        return True
    if isinstance(exc, APIError):
        code: Optional[int] = getattr(exc, "status_code", None)
        if code is not None and code in (429, 500, 502, 503, 504):
            return True
    msg = str(exc).lower()
    if "429" in msg or "rate limit" in msg or "timeout" in msg or "unavailable" in msg:
        return True
    return False


def _retry_after_from_exc(exc: BaseException) -> Optional[float]:
    if not isinstance(exc, APIError):
        return None
    resp: Any = getattr(exc, "response", None)
    if resp is not None and hasattr(resp, "headers") and resp.headers is not None:
        h: Any = resp.headers.get("retry-after")
        if h:
            try:
                return float(h)
            except (TypeError, ValueError):
                return None
    return None


def _backoff(attempt: int, cfg: GroundTruthTierRetry, exc: BaseException) -> float:
    ra = _retry_after_from_exc(exc)
    if ra is not None and ra > 0:
        return min(cfg.max_delay_sec, ra + random.uniform(0, cfg.jitter_sec))
    return min(
        cfg.max_delay_sec,
        cfg.base_delay_sec * (2 ** (attempt - 1)) + random.uniform(0, cfg.jitter_sec),
    )


async def get_topic_logprobs(
    client: AsyncOpenAI,
    model: str,
    post_text: str,
    topic: str,
    category: str,
    llm_semaphore: asyncio.Semaphore,
    timeout_sec: float,
    retry_cfg: GroundTruthTierRetry,
    max_text_chars: int,
    *,
    include_post_body: bool = True,
) -> Dict[str, Any]:
    if include_post_body:
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
    for attempt in range(1, retry_cfg.max_attempts + 1):
        async with llm_semaphore:
            try:
                if model_supports_logprobs(model):
                    try:
                        response = await client.chat.completions.create(
                            model=model,
                            messages=[
                                {"role": "system", "content": system_msg},
                                {"role": "user", "content": user_msg},
                            ],
                            max_tokens=5,
                            temperature=0,
                            logprobs=True,
                            top_logprobs=5,
                            timeout=timeout_sec,
                        )
                        ch = response.choices[0]
                        logprobs_data = getattr(ch, "logprobs", None)
                        yes_lp, no_lp, yes_tok, no_tok = yes_no_logprobs_from_completion(logprobs_data)
                        prob_yes, prob_no = normalize_yes_no_probs(yes_lp, no_lp)

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
                                model=model,
                                messages=[
                                    {"role": "system", "content": system_msg},
                                    {"role": "user", "content": user_msg},
                                ],
                                max_tokens=5,
                                temperature=0,
                                timeout=timeout_sec,
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
                        model=model,
                        messages=[
                            {"role": "system", "content": system_msg},
                            {"role": "user", "content": user_msg},
                        ],
                        max_tokens=5,
                        temperature=0,
                        timeout=timeout_sec,
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
                if _is_retryable(e) and attempt < retry_cfg.max_attempts:
                    delay = _backoff(attempt, retry_cfg, e)
                    log.warning(
                        "topic logprobs attempt %s/%s topic=%r: %s — retry in %.1fs",
                        attempt,
                        retry_cfg.max_attempts,
                        topic[:48],
                        e,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                if not _is_retryable(e):
                    return {
                        "yes": 0.0,
                        "no": 0.0,
                        "logprob_yes": _LP_FALLBACK,
                        "logprob_no": _LP_FALLBACK,
                        "token_yes": None,
                        "token_no": None,
                        "error": str(e),
                    }
                if attempt >= retry_cfg.max_attempts:
                    return {
                        "yes": 0.0,
                        "no": 0.0,
                        "logprob_yes": _LP_FALLBACK,
                        "logprob_no": _LP_FALLBACK,
                        "token_yes": None,
                        "token_no": None,
                        "error": str(e),
                    }
    return {
        "yes": 0.0,
        "no": 0.0,
        "logprob_yes": _LP_FALLBACK,
        "logprob_no": _LP_FALLBACK,
        "token_yes": None,
        "token_no": None,
        "error": str(last_err) if last_err else "unexpected",
    }
