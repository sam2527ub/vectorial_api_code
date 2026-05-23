"""Tag a single post with dimensions via AI gateway (gateway call + parse)."""
import asyncio
import math
from typing import Any, Dict, List
from app.config import logger
from app.utils.message_utils import split_prompt_into_messages
from app.services.ai_gateway_service import ai_gateway
from app.services.ai_gateway_service.utils.gateway_utils import parse_json_response
from .rate_limiter import RateLimiter
from .dimension_extractor import DimensionExtractor


def _title_and_content(post: Dict[str, Any]) -> tuple:
    """Extract title and content from post (Reddit or LinkedIn shape)."""
    title = post.get("title", "")
    if not title:
        article = post.get("article", {})
        if isinstance(article, dict):
            title = article.get("title", "")
        if not title:
            reshared = post.get("resharedPost", {})
            if isinstance(reshared, dict):
                nested = reshared.get("article", {})
                if isinstance(nested, dict):
                    title = nested.get("title", "")
    content = post.get("text", "") or post.get("body", "")
    return title, content


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
    post: Dict[str, Any],
    dimensions: List[str],
    trait_titles: List[str]
) -> Dict[str, Any]:
    """Create fallback result for post tagging."""
    fallback = DimensionExtractor.create_fallback_result(post, dimensions)
    fallback["traits"] = {
        trait: {"value": "no", "confidence": 0.0}
        for trait in trait_titles
    }
    fallback["usefulness_score"] = 0.0
    return fallback


async def tag_single_post(
    post: Dict[str, Any],
    dimensions: List[str],
    trait_titles: List[str],
    prompt_template: Any,
    model: str,
    rate_limiter: RateLimiter,
) -> Dict[str, Any]:
    """
    Tag one post with dimensions. Returns fallback on failure (SDK handles retries).
    """
    try:
        return await _tag_single_post_attempt(
            post, dimensions, trait_titles, prompt_template, model, rate_limiter
        )
    except Exception as e:
        logger.error(
            f"Error tagging dimensions for post {post.get('id', 'unknown')}: {e}"
        )
        # Slightly longer backoff on 429 before returning fallback (lets rate limit recover)
        error_msg = str(e).lower()
        if "rate limit" in error_msg or "429" in error_msg:
            await asyncio.sleep(2.0)
        return _create_fallback_result(post, dimensions, trait_titles)


async def _tag_single_post_attempt(
    post: Dict[str, Any],
    dimensions: List[str],
    trait_titles: List[str],
    prompt_template: Any,
    model: str,
    rate_limiter: RateLimiter,
) -> Dict[str, Any]:
    """One attempt: build messages, call gateway, parse response."""
    title, content = _title_and_content(post)
    full_prompt = prompt_template.format(title=title, content=content)
    system_message, user_prompt = split_prompt_into_messages(full_prompt)
    messages = []
    if system_message:
        messages.append({"role": "system", "content": system_message})
    messages.append({"role": "user", "content": user_prompt})

    estimated_input = len(full_prompt) // 4
    estimated_output = (len(dimensions) + len(trait_titles)) * 20
    await rate_limiter.wait_if_needed(estimated_input + estimated_output)

    post_id = post.get("id", "unknown")
    chat_completion = await ai_gateway.call_via_gateway(
        context_id=post_id,
        messages=messages,
        max_tokens=estimated_output + 100,
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

    if hasattr(chat_completion, "usage"):
        usage = chat_completion.usage
        actual = (usage.prompt_tokens or 0) + (usage.completion_tokens or 0)
        rate_limiter.record_usage(actual)

    response_text = chat_completion.choices[0].message.content.strip()
    try:
        parsed = parse_json_response(response_text, logger=logger)
    except Exception as e:
        logger.error(
            f"Failed to parse JSON for post {post_id}: {e}. Response: {response_text[:200]}"
        )
        return _create_fallback_result(post, dimensions, trait_titles)

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

    dimensions_result = DimensionExtractor.extract_dimensions(
        post, dimension_values, chat_completion, response_text, dimensions
    )
    traits_result = DimensionExtractor.extract_section_values(
        post, trait_values, chat_completion, response_text, trait_titles
    )
    return {
        "post": post,
        "dimensions": dimensions_result,
        "traits": traits_result,
        "usefulness_score": usefulness_score,
    }
