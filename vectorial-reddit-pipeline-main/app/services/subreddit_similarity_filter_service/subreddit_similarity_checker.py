"""Subreddit similarity checking via AI gateway (or direct Groq when gateway disabled)."""
import asyncio
import os
import random
from typing import List, Dict, Optional, Any
from app.config import logger
from app.utils.message_utils import split_prompt_into_messages
from app.services.ai_gateway_service import ai_gateway
from app.services.subreddit_similarity_filter_service.config import SubredditFilterConfig
from app.services.subreddit_similarity_filter_service.prompts import reddit_subreddit_semantic_similarity_prompt
from app.services.subreddit_similarity_filter_service.utils.data_extractors import normalize_subreddit_name

SUBREDDIT_SIMILARITY_MAX_TOKENS = 4096


def _build_messages(user_subreddits: List[str], matched_subreddits: List[str]) -> List[Dict[str, str]]:
    """Build system and user messages from the similarity prompt."""
    matched_list_str = "\n".join([f"- {s}" for s in matched_subreddits])
    user_list_str = "\n".join([f"- {s}" for s in user_subreddits])
    full_prompt = reddit_subreddit_semantic_similarity_prompt.format(
        matched_subreddits=matched_list_str,
        user_subreddits=user_list_str
    )
    system_message, user_prompt = split_prompt_into_messages(full_prompt)
    messages = []
    if system_message:
        messages.append({"role": "system", "content": system_message})
    messages.append({"role": "user", "content": user_prompt})
    return messages


def _result_to_similarity_map(result: Dict[str, Any], user_subreddits: List[str]) -> Dict[str, bool]:
    """Convert gateway JSON result to Dict[str, bool] with normalized keys."""
    similarity_map = {}
    for key, value in result.items():
        normalized_key = normalize_subreddit_name(key)
        similarity_map[normalized_key] = bool(value)
    for user_sub in user_subreddits:
        normalized_user_sub = normalize_subreddit_name(user_sub)
        if normalized_user_sub not in similarity_map:
            similarity_map[normalized_user_sub] = False
    return similarity_map


def _groq_fallback_model(config: SubredditFilterConfig) -> str:
    """Resolve Groq model for direct API fallback (when gateway disabled)."""
    if config and getattr(config, "groq_model", None):
        model = config.groq_model
        return model if model.startswith("groq/") else f"groq/{model}"
    return "groq/llama-3.3-70b-versatile"


def is_ai_configured() -> bool:
    """True if we can make similarity calls (gateway enabled or GROQ_API_KEY for fallback)."""
    return getattr(ai_gateway, "enabled", False) or bool(os.getenv("GROQ_API_KEY"))


class SimilarityChecker:
    """Handles subreddit similarity checking via AI gateway (direct Groq when gateway disabled)."""

    def __init__(self):
        self.config = SubredditFilterConfig()

    def _check_configured(self) -> None:
        if not is_ai_configured():
            raise ValueError(
                "AI not configured. Set USE_AI_GATEWAY and gateway API key, or GROQ_API_KEY for direct fallback."
            )

    async def _check_similarity_batch_via_gateway(
        self,
        user_subreddits: List[str],
        matched_subreddits: List[str],
    ) -> Dict[str, bool]:
        """Single-batch similarity call via ai_gateway (gateway or direct Groq fallback)."""
        config = self.config
        max_retries = config.max_retries
        current_batch = user_subreddits
        fallback_model = _groq_fallback_model(config)

        for attempt in range(max_retries):
            try:
                messages = _build_messages(current_batch, matched_subreddits)
                result = await ai_gateway.call_via_gateway(
                    context_id="subreddit-similarity",
                    messages=messages,
                    max_tokens=SUBREDDIT_SIMILARITY_MAX_TOKENS,
                    default_model=None,
                    fallback_models=None,
                    config_default_attr="subreddit_similarity_default",
                    config_fallbacks_attr="subreddit_similarity_fallbacks",
                    hardcoded_default="groq/llama-3.3-70b-versatile",
                    return_text=False,
                    direct_api_fallback_model=fallback_model,
                )
                if isinstance(result, dict):
                    return _result_to_similarity_map(result, user_subreddits)
                logger.error("Gateway returned non-dict result")
                return {normalize_subreddit_name(s): False for s in user_subreddits}
            except Exception as e:
                error_str = str(e).lower()
                is_rate = "rate" in error_str or "429" in error_str or "too many" in error_str
                is_context = (
                    "context" in error_str or "token" in error_str
                    or "length" in error_str or "too long" in error_str
                )
                if is_rate and attempt < max_retries - 1:
                    delay = config.initial_retry_delay * (2 ** attempt) + random.uniform(0, 1)
                    logger.warning(
                        f"Rate limit hit, retry {attempt + 1}/{max_retries} in {delay:.1f}s"
                    )
                    await asyncio.sleep(delay)
                    continue
                if is_context and attempt < max_retries - 1 and len(current_batch) > 5:
                    new_size = len(current_batch) // 2
                    logger.warning(
                        f"Context limit hit, reducing batch from {len(current_batch)} to {new_size}"
                    )
                    results = {}
                    for i in range(0, len(user_subreddits), new_size):
                        small_batch = user_subreddits[i : i + new_size]
                        await asyncio.sleep(config.batch_delay)
                        try:
                            batch_result = await self._check_similarity_batch_via_gateway(
                                small_batch, matched_subreddits
                            )
                            results.update(batch_result)
                        except Exception as inner_e:
                            logger.error(f"Failed to process sub-batch: {inner_e}")
                            for s in small_batch:
                                results[normalize_subreddit_name(s)] = False
                    return results
                logger.error(f"AI gateway call failed: {e}")
                return {normalize_subreddit_name(s): False for s in user_subreddits}

        return {normalize_subreddit_name(s): False for s in user_subreddits}

    async def check_similarity_batch(
        self,
        user_subreddits: List[str],
        matched_subreddits: List[str],
        batch_size: Optional[int] = None
    ) -> Dict[str, bool]:
        """Check which user subreddits are similar to matched subreddits."""
        self._check_configured()

        batch_size = batch_size or self.config.batch_size_default

        normalized_matched = [normalize_subreddit_name(s) for s in matched_subreddits]
        normalized_user = [normalize_subreddit_name(s) for s in user_subreddits]
        normalized_matched = list(dict.fromkeys(normalized_matched))
        normalized_user = list(dict.fromkeys(normalized_user))

        if len(normalized_matched) > self.config.max_matched_subreddits_in_prompt:
            logger.warning(
                f"Limiting matched subreddits from {len(normalized_matched)} "
                f"to {self.config.max_matched_subreddits_in_prompt}"
            )
            normalized_matched = normalized_matched[:self.config.max_matched_subreddits_in_prompt]

        similarity_map = {}
        exact_matches = set()
        to_check = []
        matched_set = set(normalized_matched)
        for user_sub in normalized_user:
            if user_sub in matched_set:
                similarity_map[user_sub] = True
                exact_matches.add(user_sub)
            else:
                to_check.append(user_sub)

        logger.info(
            f"Found {len(exact_matches)} exact matches, "
            f"{len(to_check)} subreddits to check with AI provider"
        )

        if not to_check:
            return similarity_map

        total_batches = (len(to_check) + batch_size - 1) // batch_size
        for i in range(0, len(to_check), batch_size):
            batch_num = (i // batch_size) + 1
            batch = to_check[i:i + batch_size]
            logger.info(
                f"Processing AI batch {batch_num}/{total_batches} ({len(batch)} subreddits)"
            )
            if i > 0:
                delay = self.config.batch_delay + random.uniform(0, 0.3)
                await asyncio.sleep(delay)
            batch_results = await self._check_similarity_batch_via_gateway(
                batch, normalized_matched
            )
            similarity_map.update(batch_results)

        logger.info(
            f"AI similarity check complete: "
            f"{sum(1 for v in similarity_map.values() if v)} similar out of {len(similarity_map)}"
        )
        return similarity_map
