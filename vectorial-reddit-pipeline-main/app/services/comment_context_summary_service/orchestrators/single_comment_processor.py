"""Process a single comment with context summary via AI Gateway."""
from typing import Any, Dict, List
from app.config import logger
from app.services.comment_context_summary_service.clients.ai import get_ai_client
from app.services.comment_context_summary_service.config import CommentContextSummaryConfig
from app.services.comment_context_summary_service.utils.context_builder import build_context_for_comment
from app.services.comment_context_summary_service.utils.rate_limiter import RateLimiter


async def process_single_comment(
    comment: Dict[str, Any],
    post_url_to_items: Dict[str, List[Dict[str, Any]]],
    config: CommentContextSummaryConfig,
    rate_limiter: RateLimiter,
    ai_client: Any,
) -> Dict[str, Any]:
    """
    Process one comment: build context and generate summary.
    
    Returns:
        {
            "status": "success" | "skipped" | "error",
            "comment": comment (with context and summary if successful),
            "reason": str (if skipped),
            "error": str (if error)
        }
    """
    if not isinstance(comment, dict):
        return {"status": "skipped", "reason": "not_dict", "comment": comment}
    
    comment_id = comment.get("id", "unknown")
    post_url = comment.get("postUrl")
    
    if not post_url:
        return {"status": "skipped", "reason": "no_post_url", "comment": comment}
    
    post_url_normalized = post_url.rstrip("/")
    scraper_items = post_url_to_items.get(post_url_normalized, [])
    
    if not scraper_items:
        return {"status": "skipped", "reason": "no_scraper_data", "comment": comment}
    
    context = build_context_for_comment(comment, scraper_items)
    if not context:
        return {"status": "skipped", "reason": "no_context", "comment": comment}
    
    try:
        # Estimate tokens for rate limiting
        # Rough estimate: prompt length / 4 + output tokens (150)
        prompt_length = len(str(context.get("post_title", ""))) + len(str(context.get("post_body", ""))) + len(str(context.get("parent_comment_body", "")))
        estimated_tokens = (prompt_length // 4) + 150
        
        # Wait if rate limit would be exceeded
        await rate_limiter.wait_if_needed(estimated_tokens)
        
        # Generate summary via AI Gateway
        summary = await ai_client.summarize_comment_context(context)
        
        # Record actual token usage (we'll get this from gateway response if available)
        # For now, use estimated tokens (we can enhance this later to get actual usage)
        await rate_limiter.record_usage(estimated_tokens)
        
        # Add context and summary to comment
        comment["context"] = context
        comment["context_summary"] = summary
        
        return {"status": "success", "comment": comment}
        
    except Exception as e:
        logger.error(f"Error processing comment {comment_id}: {e}", exc_info=True)
        return {"status": "error", "error": str(e), "comment": comment}
