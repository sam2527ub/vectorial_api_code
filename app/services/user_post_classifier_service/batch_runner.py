"""Run classification in batches (multiple posts per API call). Calls AI Gateway directly."""
import asyncio
from typing import Any, Dict, List, Optional

from app.config import logger
from app.services.ai_gateway_service import ai_gateway

from .config import UserPostClassifierConfig
from .utils.parser_helpers import extract_post_texts
from .utils.prompt_builder import build_classifier_prompts
from .utils.response_matcher import match_classifications_to_posts
from .utils.response_normalizer import normalize_and_pad


async def classify_posts_batch(
    posts: List[Dict[str, Any]],
    classifier_name: str,
    classifier_prompt: str,
    classifier_description: str,
    classifier_labels: List[str],
    classifier_examples: Optional[Any] = None,
    batch_size: int = 20,
    config: Optional[UserPostClassifierConfig] = None,
) -> List[Dict[str, Any]]:
    """
    Classify multiple posts in batches. Each batch = one ai_gateway.call_via_gateway call.
    Same pattern as comment_context_summary_service (no client, direct ai_gateway).
    """
    cfg = config or UserPostClassifierConfig()
    posts_texts = extract_post_texts(posts)
    if not posts_texts:
        return []

    results: List[Dict[str, Any]] = []
    default_label = classifier_labels[0] if classifier_labels else "Unknown"
    default_scores = {l: 0.0 for l in classifier_labels}
    if default_label in default_scores:
        default_scores[default_label] = 0.5
    default_item = {"label": default_label, "score": 0.5, "allScores": default_scores}

    for i in range(0, len(posts_texts), batch_size):
        batch_texts = posts_texts[i : i + batch_size]
        batch_num = (i // batch_size) + 1
        total_batches = (len(posts_texts) + batch_size - 1) // batch_size
        logger.info(f"Classifier batch {batch_num}/{total_batches} ({len(batch_texts)} posts)")

        try:
            batch_results = await _classify_one_batch_via_gateway(
                posts_texts=batch_texts,
                classifier_name=classifier_name,
                classifier_prompt=classifier_prompt,
                classifier_description=classifier_description,
                classifier_labels=classifier_labels,
                classifier_examples=classifier_examples,
                config=cfg,
            )
            results.extend(batch_results)
        except Exception as e:
            logger.error(f"Classifier batch {batch_num} error: {e}")
            for _ in batch_texts:
                results.append(default_item.copy())

        if i + batch_size < len(posts_texts):
            await asyncio.sleep(1.0)

    return results


async def _classify_one_batch_via_gateway(
    posts_texts: List[str],
    classifier_name: str,
    classifier_prompt: str,
    classifier_description: str,
    classifier_labels: List[str],
    classifier_examples: Optional[Any],
    config: UserPostClassifierConfig,
) -> List[Dict[str, Any]]:
    """Single batch: build messages, call ai_gateway.call_via_gateway, parse and normalize."""
    if not posts_texts:
        return []

    system_prompt, user_prompt = build_classifier_prompts(
        posts_texts, classifier_name, classifier_prompt,
        classifier_description, classifier_labels, classifier_examples,
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    num_posts = len(posts_texts)
    max_retries = 5
    max_count_mismatch_retries = 2
    base_delay = 2

    for attempt in range(max_retries):
        count_mismatch_retry = 0
        while count_mismatch_retry <= max_count_mismatch_retries:
            try:
                result = await ai_gateway.call_via_gateway(
                    context_id=f"classifier_batch_{attempt}_{count_mismatch_retry}",
                    messages=messages,
                    max_tokens=8192,
                    model=None,
                    default_model=None,
                    fallback_models=None,
                    config_default_attr="user_post_classifier_default",
                    config_fallbacks_attr="user_post_classifier_fallbacks",
                    hardcoded_default=config.classifier_model,
                    validate_summary=False,
                    return_text=False,
                    direct_api_fallback_model=config.classifier_model,
                )
                if not isinstance(result, dict):
                    raise ValueError(f"Gateway returned non-dict: {type(result)}")
                raw_list = result.get("classifications", [])
                if not isinstance(raw_list, list):
                    raw_list = []
                if len(raw_list) != num_posts and count_mismatch_retry < max_count_mismatch_retries:
                    count_mismatch_retry += 1
                    await asyncio.sleep(0.5)
                    continue
                if len(raw_list) != num_posts:
                    raw_list = match_classifications_to_posts(raw_list, num_posts, classifier_labels)
                return normalize_and_pad(raw_list, num_posts, classifier_labels)
            except Exception as e:
                if count_mismatch_retry < max_count_mismatch_retries:
                    count_mismatch_retry += 1
                    await asyncio.sleep(0.5)
                    continue
                raise
        if attempt < max_retries - 1:
            delay = base_delay * (2 ** attempt)
            logger.warning(f"Classifier retry {attempt + 1}/{max_retries}, waiting {delay}s")
            await asyncio.sleep(delay)

    raise RuntimeError("Failed to get classifications from AI Gateway after retries")
