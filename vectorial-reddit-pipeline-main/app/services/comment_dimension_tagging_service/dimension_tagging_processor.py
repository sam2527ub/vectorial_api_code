"""Dimension tagging processor: parallel comment tagging via AI gateway."""
import asyncio
from typing import Dict, Any, List, Optional
from app.config import logger
from .utils.rate_limiter import RateLimiter
from .utils.single_comment_tagger import tag_single_comment
from .clients.ai.openai_dimension_extractor import OpenAIDimensionExtractor


class DimensionTaggingProcessor:
    """Tags comments with dimensions via AI gateway (logprobs for OpenAI models)."""

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
        comments: List[Dict[str, Any]],
        dimensions: List[str],
        trait_titles: List[str],
        prompt_template: Any,
        config: Any,
    ) -> List[Dict[str, Any]]:
        """
        Tag comments with dimensions using AI gateway. Processes comments in parallel with rate limiting.

        Returns list of results, each containing:
        - comment: Original comment
        - dimensions: Dict with dimension -> {"value": "yes/no", "confidence": float}
        """
        return asyncio.run(
            self._tag_dimensions_async(comments, dimensions, trait_titles, prompt_template, config)
        )

    async def _tag_dimensions_async(
        self,
        comments: List[Dict[str, Any]],
        dimensions: List[str],
        trait_titles: List[str],
        prompt_template: Any,
        config: Any,
    ) -> List[Dict[str, Any]]:
        rate_limiter = self._get_rate_limiter(config)
        model = getattr(config, "openai_model", "gpt-4o-mini")
        max_concurrent = getattr(config, "max_concurrent_requests", 30)
        total_comments = len(comments)
        log_every = max(1, min(10, total_comments // 4)) if total_comments else 1
        done_count: List[int] = [0]
        semaphore = asyncio.Semaphore(max_concurrent)

        async def process_one(comment: Dict[str, Any]) -> Dict[str, Any]:
            async with semaphore:
                try:
                    return await tag_single_comment(
                        comment, dimensions, trait_titles, prompt_template, model, rate_limiter
                    )
                finally:
                    done_count[0] += 1
                    n = done_count[0]
                    pct = (100 * n) // total_comments if total_comments else 0
                    if n % log_every == 0 or n == total_comments or pct in (25, 50, 75):
                        logger.debug(
                            f"Comment dimension tagging progress: {n}/{total_comments} comments ({pct}%)"
                        )

        tasks = [process_one(comment) for comment in comments]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        processed = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(
                    f"Error processing comment {comments[i].get('id', 'unknown')}: {result}"
                )
                processed.append(
                    {
                        **OpenAIDimensionExtractor.create_fallback_result(comments[i], dimensions),
                        "traits": {
                            trait: {"value": "no", "confidence": 0.0}
                            for trait in trait_titles
                        },
                        "usefulness_score": 0.0,
                    }
                )
            else:
                processed.append(result)
        return processed
