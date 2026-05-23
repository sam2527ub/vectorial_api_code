"""Dimension tagging processor: parallel post tagging via AI gateway."""
import asyncio
from typing import Dict, Any, List, Optional
from app.config import logger
from .utils.rate_limiter import RateLimiter
from .utils.single_post_tagger import tag_single_post


class DimensionTaggingProcessor:
    """Tags posts with dimensions via AI gateway (logprobs for OpenAI models)."""

    def __init__(self):
        self._rate_limiter: Optional[RateLimiter] = None

    def _get_rate_limiter(self, config: Any) -> RateLimiter:
        if self._rate_limiter is None:
            self._rate_limiter = RateLimiter(
                rpm_limit=getattr(config, "openai_rpm_limit", 500),
                tpm_limit=getattr(config, "openai_tpm_limit", 2000000),
                window_seconds=getattr(config, "openai_rate_limit_window", 60),
            )
        return self._rate_limiter

    def tag_dimensions(
        self,
        posts: List[Dict[str, Any]],
        dimensions: List[str],
        trait_titles: List[str],
        prompt_template: Any,
        config: Any,
    ) -> List[Dict[str, Any]]:
        """
        Tag posts with dimensions using AI gateway. Processes posts in parallel with rate limiting.

        Returns list of results, each containing:
        - post: Original post
        - dimensions: Dict with dimension -> {"value": "yes/no", "confidence": float}
        """
        return asyncio.run(
            self._tag_dimensions_async(posts, dimensions, trait_titles, prompt_template, config)
        )

    async def _tag_dimensions_async(
        self,
        posts: List[Dict[str, Any]],
        dimensions: List[str],
        trait_titles: List[str],
        prompt_template: Any,
        config: Any,
    ) -> List[Dict[str, Any]]:
        rate_limiter = self._get_rate_limiter(config)
        model = getattr(config, "openai_model", "gpt-4o-mini")
        max_concurrent = getattr(config, "max_concurrent_requests", 30)
        total_posts = len(posts)
        log_every = max(1, min(10, total_posts // 4)) if total_posts else 1
        done_count: List[int] = [0]
        semaphore = asyncio.Semaphore(max_concurrent)

        async def process_one(post: Dict[str, Any]) -> Dict[str, Any]:
            async with semaphore:
                try:
                    return await tag_single_post(
                        post, dimensions, trait_titles, prompt_template, model, rate_limiter
                    )
                finally:
                    done_count[0] += 1
                    n = done_count[0]
                    pct = (100 * n) // total_posts if total_posts else 0
                    if n % log_every == 0 or n == total_posts or pct in (25, 50, 75):
                        logger.debug(
                            f"Dimension tagging progress: {n}/{total_posts} posts ({pct}%)"
                        )

        tasks = [process_one(post) for post in posts]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        processed = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(
                    f"Error processing post {posts[i].get('id', 'unknown')}: {result}"
                )
                processed.append({
                    "post": posts[i],
                    "dimensions": {
                        dim: {"value": "no", "confidence": 0.0}
                        for dim in dimensions
                    },
                    "traits": {
                        trait: {"value": "no", "confidence": 0.0}
                        for trait in trait_titles
                    },
                    "usefulness_score": 0.0,
                })
            else:
                processed.append(result)
        return processed
