"""Calls AI Gateway (Groq) to summarize LinkedIn comment context into a short summary."""

from typing import Dict, Optional

from app.config import logger
from app.services.ai_gateway_service import ai_gateway

from app.services.comment_context_summary_service.config import CommentContextSummaryConfig


async def summarize_comment_context_via_ai(
    profile_id: str,
    comment_id: str,
    context: Dict[str, Optional[str]],
    config: CommentContextSummaryConfig,
) -> str:
    """
    Call AI Gateway (Groq) to get a JSON {summary: str} for the given comment context.
    Raises ValueError if result is not a dict or summary is empty.
    """
    post_body = context.get("post_body") or ""
    parent_body = context.get("parent_comment_body")
    parent_text = parent_body if parent_body else "Direct reply to the post"

    prompt = (
        "You are summarizing LinkedIn comment context.\n"
        "Return STRICTLY a JSON object with a single key `summary`.\n"
        "The value must be a 1–2 sentence summary of the context, "
        "capturing the main topic, what is being discussed, and what the commenter is responding to.\n\n"
        f"Post body:\n{post_body}\n\n"
        f"Parent comment (or description of what is being replied to):\n{parent_text}\n"
    )

    messages = [
        {"role": "system", "content": "You return only valid JSON in the format {\"summary\": \"...\"}."},
        {"role": "user", "content": prompt},
    ]

    result = await ai_gateway.call_via_gateway(
        context_id=f"{profile_id}:{comment_id}",
        messages=messages,
        max_tokens=200,
        model=None,
        default_model=None,
        fallback_models=None,
        config_default_attr="comment_context_summary_default",
        config_fallbacks_attr="comment_context_summary_fallbacks",
        hardcoded_default=config.groq_model,
        validate_summary=False,
        return_text=False,
    )

    if not isinstance(result, dict):
        logger.warning("Unexpected Groq result type for comment %s: %r", comment_id, type(result))
        raise ValueError("Groq result was not a JSON object")

    summary = (result.get("summary") or "").strip()
    if not summary:
        raise ValueError("Groq returned empty summary")
    return summary
