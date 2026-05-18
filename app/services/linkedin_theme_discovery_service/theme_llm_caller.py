"""
Theme-discovery JSON calls go through the shared ``ai_gateway`` (see ``user_post_classifier`` batch path).

Each request uses ``response_format: json_object`` and tier-specific model rotation
(``config.yaml`` → ``linkedin_theme_discovery_tier1`` / ``linkedin_theme_discovery_tier2``).
"""

from __future__ import annotations

import asyncio
import random
from typing import Any, Dict, Optional, Union

from openai import APIConnectionError, APIError, APITimeoutError, RateLimitError

from app.config import logger
from app.services.ai_gateway_service import ai_gateway
from .tier_production_config import LlmRetryConfig

THEME_JSON_FORMAT: Dict[str, str] = {"type": "json_object"}
THEME_LLM_TEMPERATURE = 0.4

_T1D = "linkedin_theme_discovery_tier1_default"
_T1F = "linkedin_theme_discovery_tier1_fallbacks"
_T2D = "linkedin_theme_discovery_tier2_default"
_T2F = "linkedin_theme_discovery_tier2_fallbacks"


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


async def call_theme_json_via_gateway(
    context_id: str,
    system: str,
    user: str,
    max_tokens: int,
    is_tier2: bool,
    model: str,
    llm_retry: LlmRetryConfig,
) -> Dict[str, Any]:
    messages: list[dict[str, str]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    d_attr, f_attr = (
        (_T2D, _T2F) if is_tier2 else (_T1D, _T1F)
    )
    last: Optional[BaseException] = None
    for attempt in range(1, llm_retry.max_attempts + 1):
        try:
            out: Union[dict[str, Any], str, Any] = await ai_gateway.call_via_gateway(
                context_id=f"{context_id}__a{attempt}",
                messages=messages,
                max_tokens=max_tokens,
                model=model,
                default_model=None,
                fallback_models=None,
                config_default_attr=d_attr,
                config_fallbacks_attr=f_attr,
                hardcoded_default="openai/gpt-4o-mini",
                validate_summary=False,
                return_text=False,
                direct_api_fallback_model=model,
                response_format=THEME_JSON_FORMAT,
                temperature=THEME_LLM_TEMPERATURE,
            )
            if not isinstance(out, dict):
                raise TypeError(f"theme LLM: expected dict from gateway, got {type(out)}")
            return out
        except Exception as e:
            last = e
            if not _is_retryable(e) or attempt >= llm_retry.max_attempts:
                raise
            ra = _retry_after_from_exc(e)
            if ra is not None and ra > 0:
                delay = min(llm_retry.max_delay_sec, ra + random.uniform(0, llm_retry.jitter_sec))
            else:
                delay = min(
                    llm_retry.max_delay_sec,
                    llm_retry.base_delay_sec * (2 ** (attempt - 1)) + random.uniform(0, llm_retry.jitter_sec),
                )
            logger.warning(
                "Theme discovery LLM (gateway) attempt %s/%s failed: %s — retry in %.1fs",
                attempt,
                llm_retry.max_attempts,
                e,
                delay,
            )
            await asyncio.sleep(delay)
    if last is not None:
        raise last
    raise RuntimeError("theme LLM: exhausted retries with no last exception")
