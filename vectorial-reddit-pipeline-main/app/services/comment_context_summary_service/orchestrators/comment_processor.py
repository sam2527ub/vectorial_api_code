"""Comment processor: parallel comment enrichment via AI Gateway."""
import asyncio
from typing import Dict, Any, List, Optional
from app.config import logger
from app.services.comment_context_summary_service.config import CommentContextSummaryConfig
from app.services.comment_context_summary_service.utils.rate_limiter import RateLimiter
from app.services.comment_context_summary_service.clients.ai import get_ai_client
from .single_comment_processor import process_single_comment


class CommentProcessor:
    """Processes comments with context summaries via AI Gateway."""

    def __init__(self):
        self._rate_limiter: Optional[RateLimiter] = None

    def _get_rate_limiter(self, config: CommentContextSummaryConfig) -> RateLimiter:
        """Get or create rate limiter instance."""
        if self._rate_limiter is None:
            self._rate_limiter = RateLimiter(
                rpm_limit=getattr(config, "groq_rpm_limit", 30),
                tpm_limit=getattr(config, "groq_tpm_limit", 1000000),
                window_seconds=getattr(config, "groq_rate_limit_window", 60),
            )
        return self._rate_limiter

    async def process_comments(
        self,
        comments: List[Dict[str, Any]],
        post_url_to_items: Dict[str, List[Dict[str, Any]]],
        config: CommentContextSummaryConfig,
    ) -> List[Dict[str, Any]]:
        """
        Process comments with context summaries using AI Gateway.
        Processes all comments in parallel with rate limiting.

        Args:
            comments: List of comment dictionaries to process
            post_url_to_items: Map of post URLs to scraper items
            config: Configuration object

        Returns:
            List of results, each containing:
            - status: "success" | "skipped" | "error"
            - comment: Original comment (with context and summary if successful)
            - reason: str (if skipped)
            - error: str (if error)
        """
        if not comments:
            return []

        rate_limiter = self._get_rate_limiter(config)
        ai_client = get_ai_client(config)
        max_concurrent = getattr(config, "max_concurrent_api_calls", 12)
        
        total_comments = len(comments)
        log_every = max(1, min(10, total_comments // 4)) if total_comments else 1
        done_count: List[int] = [0]
        semaphore = asyncio.Semaphore(max_concurrent)

        async def process_one(comment: Dict[str, Any]) -> Dict[str, Any]:
            """Process a single comment with semaphore control."""
            async with semaphore:
                try:
                    return await process_single_comment(
                        comment, post_url_to_items, config, rate_limiter, ai_client
                    )
                finally:
                    done_count[0] += 1
                    n = done_count[0]
                    pct = (100 * n) // total_comments if total_comments else 0
                    if n % log_every == 0 or n == total_comments or pct in (25, 50, 75):
                        logger.info(
                            f"Comment processing progress: {n}/{total_comments} comments ({pct}%)"
                        )

        tasks = [process_one(comment) for comment in comments]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        processed = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(
                    f"Error processing comment {comments[i].get('id', 'unknown')}: {result}"
                )
                processed.append({
                    "status": "error",
                    "error": str(result),
                    "comment": comments[i],
                })
            else:
                processed.append(result)
        
        return processed
