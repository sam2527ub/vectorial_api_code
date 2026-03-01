"""Build context dict for a LinkedIn comment from comment.json for AI summarization."""

from typing import Any, Dict, Optional

from app.config import logger

from app.services.comment_context_summary_service.config import CommentContextSummaryConfig
from app.services.comment_context_summary_service.utils.text_truncation import (
    truncate_text_at_max_length,
)


def build_context_for_comment(
    comment: Dict[str, Any],
    config: Optional[CommentContextSummaryConfig] = None,
) -> Optional[Dict[str, Optional[str]]]:
    """
    Build context dict for a LinkedIn comment from comment.json.

    Expects fields: post_body, parent_comment_body (optional).
    Truncates to config limits and returns None if no usable text.
    """
    cfg = config or CommentContextSummaryConfig()

    post_body_raw = comment.get("post_body") or ""
    parent_body_raw = comment.get("parent_comment_body")

    if not isinstance(post_body_raw, str):
        post_body_raw = str(post_body_raw or "")
    if parent_body_raw is not None and not isinstance(parent_body_raw, str):
        parent_body_raw = str(parent_body_raw or "")

    post_body = truncate_text_at_max_length(post_body_raw.strip(), cfg.context_max_post_length)
    parent_comment_body = (
        truncate_text_at_max_length(
            parent_body_raw.strip(), cfg.context_max_parent_comment_length
        )
        if parent_body_raw
        else None
    )

    if not post_body and not parent_comment_body:
        logger.debug("Comment has no usable text for context; skipping")
        return None

    return {
        "post_body": post_body or "",
        "parent_comment_body": parent_comment_body,
    }
