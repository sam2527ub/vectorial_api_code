"""Utilities for building comment context for dimension tagging."""
from typing import Dict, Any, Optional
from app.config import logger


def build_context_text_for_tagging(comment: Dict[str, Any]) -> Optional[str]:
    """
    Build context text for dimension tagging from comment.
    
    ONLY uses context_summary (no fallback).
    
    Returns:
        Context summary string, or None if not available
    """
    context_summary = comment.get("context_summary")
    if context_summary and isinstance(context_summary, str) and context_summary.strip():
        return context_summary.strip()
    
    # No fallback - return None if no context_summary
    return None


def get_comment_body(comment: Dict[str, Any]) -> str:
    """
    Extract comment body text.
    
    Returns:
        Comment body text, or empty string if not available
    """
    # Reddit comments usually use "body"; LinkedIn comments often use "comment_body".
    body = comment.get("body") or comment.get("comment_body") or ""
    if body and isinstance(body, str):
        return body.strip()
    return ""


def prepare_comment_for_tagging(comment: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """
    Prepare comment data for dimension tagging prompt.
    
    Returns:
        Dict with:
        - context_text: Context summary or built context
        - comment_body: User's comment text
        Or None if comment is invalid
    """
    context_text = build_context_text_for_tagging(comment)
    if not context_text:
        return None
    
    comment_body = get_comment_body(comment)
    
    return {
        "context_text": context_text,
        "comment_body": comment_body
    }
