"""Utilities for building comment context from scraper output."""
import re
from typing import Dict, Any, Optional, List
from app.config import logger
from .comment_matcher import (
    find_post_in_scraper_output,
    flatten_comments_recursive
)


def truncate_text(text: str, max_length: int) -> str:
    """Truncate text to max_length, preserving word boundaries."""
    if not text or len(text) <= max_length:
        return text
    truncated = text[:max_length]
    last_space = truncated.rfind(' ')
    if last_space > max_length * 0.8:  # Only truncate at word if reasonable
        return truncated[:last_space] + "..."
    return truncated + "..."


def build_context_for_comment(
    comment: Dict[str, Any],
    scraper_items: List[Dict[str, Any]]
) -> Optional[Dict[str, str]]:
    """
    Build context for a comment: post title + post body + parent comment body.
    
    Returns:
        {
            "post_title": str,
            "post_body": str,
            "parent_comment_body": str | None  # None if top-level comment
        }
        or None if post has no text body
    """
    post_id = comment.get("postId")
    parent_id = comment.get("parentId")
    comment_id = comment.get("id", "unknown")
    
    if not post_id:
        logger.debug(f"[Context] Comment {comment_id}: No postId field")
        return None
    
    post = find_post_in_scraper_output(post_id, scraper_items)
    if not post:
        logger.debug(f"[Context] Comment {comment_id}: Post {post_id} not found")
        return None
    
    post_title = post.get("title", "")
    post_text = post.get("text") or post.get("textHTML")
    if not post_text:
        logger.debug(f"[Context] Comment {comment_id}: Post {post_id} has no text body")
        return None
    
    parent_comment_body = None
    
    if parent_id and parent_id.startswith("t1_"):
        short_parent_id = parent_id.replace("t1_", "")
        comment_map = {}
        for item in scraper_items:
            if isinstance(item, dict) and item.get("type") == "post":
                comments = item.get("comments", [])
                if comments:
                    comment_map.update(flatten_comments_recursive(comments))
        
        parent_comment = comment_map.get(short_parent_id)
        if parent_comment:
            parent_comment_body = parent_comment.get("body") or parent_comment.get("bodyHTML")
            if parent_comment_body and isinstance(parent_comment_body, str):
                parent_comment_body = re.sub(r'<[^>]+>', '', parent_comment_body)
                # Truncate parent comment body
                from app.services.comment_context_summary_service.config import CommentContextSummaryConfig
                config = CommentContextSummaryConfig()
                parent_comment_body = truncate_text(
                    parent_comment_body,
                    config.context_max_parent_comment_length
                )
    
    if isinstance(post_text, str):
        post_text = re.sub(r'<[^>]+>', '', post_text)
        # Truncate post body
        from app.services.comment_context_summary_service.config import CommentContextSummaryConfig
        config = CommentContextSummaryConfig()
        post_text = truncate_text(post_text, config.context_max_post_length)
    
    return {
        "post_title": post_title,
        "post_body": post_text,
        "parent_comment_body": parent_comment_body
    }
