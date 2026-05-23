"""Tag a single comment with dimensions via AI gateway (gateway call + parse)."""
import asyncio
import math
from typing import Any, Dict, List
from app.config import logger
from app.utils.message_utils import split_prompt_into_messages
from app.services.ai_gateway_service import ai_gateway
from app.services.ai_gateway_service.utils.gateway_utils import parse_json_response
from .rate_limiter import RateLimiter
from .context_builder import prepare_comment_for_tagging
from ..clients.ai.openai_dimension_extractor import OpenAIDimensionExtractor


def _normalize_usefulness_score(value: Any) -> float:
    """Normalize usefulness score into [0.0, 1.0]."""
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(score):
        return 0.0
    if score < 0.0:
        return 0.0
    if score > 1.0:
        return 1.0
    return round(score, 4)


def _create_fallback_result(
    comment: Dict[str, Any],
    dimensions: List[str],
    trait_titles: List[str]
) -> Dict[str, Any]:
    """Create fallback result for comment tagging."""
    fallback = OpenAIDimensionExtractor.create_fallback_result(comment, dimensions)
    fallback["traits"] = {
        trait: {"value": "no", "confidence": 0.0}
        for trait in trait_titles
    }
    fallback["usefulness_score"] = 0.0
    return fallback


async def tag_single_comment(
    comment: Dict[str, Any],
    dimensions: List[str],
    trait_titles: List[str],
    prompt_template: Any,
    model: str,
    rate_limiter: RateLimiter,
) -> Dict[str, Any]:
    """
    Tag one comment with dimensions. Returns fallback on failure (gateway handles retries).
    """
    try:
        return await _tag_single_comment_attempt(
            comment, dimensions, trait_titles, prompt_template, model, rate_limiter
        )
    except Exception as e:
        logger.error(
            f"Error tagging dimensions for comment {comment.get('id', 'unknown')}: {e}"
        )
        # Slightly longer backoff on 429 before returning fallback (lets rate limit recover)
        error_msg = str(e).lower()
        if "rate limit" in error_msg or "429" in error_msg:
            await asyncio.sleep(2.0)
        return _create_fallback_result(comment, dimensions, trait_titles)


async def _tag_single_comment_attempt(
    comment: Dict[str, Any],
    dimensions: List[str],
    trait_titles: List[str],
    prompt_template: Any,
    model: str,
    rate_limiter: RateLimiter,
) -> Dict[str, Any]:
    """One attempt: build messages, call gateway, parse response."""
    # Prepare comment (only if has context_summary)
    prepared = prepare_comment_for_tagging(comment)
    if not prepared or not prepared.get("context_text"):
        # No context_summary - return fallback
        return _create_fallback_result(comment, dimensions, trait_titles)
    
    context_text = prepared["context_text"]
    comment_body = prepared["comment_body"] or ""
    
    # Build prompt
    full_prompt = prompt_template.format(
        context_summary=context_text,
        comment_body=comment_body
    )
    system_message, user_prompt = split_prompt_into_messages(full_prompt)
    messages = []
    if system_message:
        messages.append({"role": "system", "content": system_message})
    messages.append({"role": "user", "content": user_prompt})

    # Estimate tokens and wait if needed (add 20% buffer like comment service)
    estimated_input_tokens = int(len(full_prompt) // 4 * 1.2)
    estimated_output_tokens = int((len(dimensions) + len(trait_titles)) * 20 * 1.2)
    estimated_total = estimated_input_tokens + estimated_output_tokens
    await rate_limiter.wait_if_needed(estimated_total)

    comment_id = comment.get("id", "unknown")
    chat_completion = await ai_gateway.call_via_gateway(
        context_id=comment_id,
        messages=messages,
        max_tokens=estimated_output_tokens + 100,
        model=model,
        config_default_attr="dimension_tagging_default",
        config_fallbacks_attr="dimension_tagging_fallbacks",
        hardcoded_default="openai/gpt-4o-mini",
        validate_summary=False,
        return_text=False,
        logprobs=True,
        top_logprobs=2,
        return_full_response=True,
        response_format={"type": "json_object"},
    )

    # Record token usage (async for comment rate limiter)
    if hasattr(chat_completion, "usage"):
        usage = chat_completion.usage
        actual_tokens = (usage.prompt_tokens or 0) + (usage.completion_tokens or 0)
        await rate_limiter.record_usage(actual_tokens)

    response_text = chat_completion.choices[0].message.content.strip()
    try:
        parsed = parse_json_response(response_text, logger=logger)
    except Exception as e:
        logger.error(
            f"Failed to parse JSON for comment {comment_id}: {e}. Response: {response_text[:200]}"
        )
        return _create_fallback_result(comment, dimensions, trait_titles)

    if isinstance(parsed, dict) and "dimensions" in parsed:
        dimension_values = parsed.get("dimensions", {})
        trait_values = parsed.get("traits", {})
        if "usefulness_score" in parsed:
            usefulness_score = _normalize_usefulness_score(parsed.get("usefulness_score"))
        else:
            # Backward compatibility for old prompt outputs
            usefulness_score = 1.0 if str(parsed.get("signal_type", "")).strip().lower() == "useful" else 0.0
    else:
        # Backward compatibility with old flat schema
        dimension_values = parsed if isinstance(parsed, dict) else {}
        trait_values = {}
        usefulness_score = 0.0

    # Extract dimensions (no boundaries needed - single comment response)
    dimensions_result = OpenAIDimensionExtractor.extract_dimensions(
        comment, dimension_values, chat_completion, response_text, dimensions
    )

    traits_result = OpenAIDimensionExtractor.extract_section_values(
        comment, trait_values, chat_completion, response_text, trait_titles
    )

    return {
        "comment": comment,
        "dimensions": dimensions_result,
        "traits": traits_result,
        "usefulness_score": usefulness_score,
    }
