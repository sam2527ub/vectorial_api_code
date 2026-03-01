"""Truncate text to a max length with word-boundary awareness."""

from typing import Optional


def truncate_text_at_max_length(text: Optional[str], max_length: int) -> Optional[str]:
    """
    Truncate text to at most max_length characters, ending at a word boundary when possible.
    Returns None if text is falsy; otherwise returns truncated string with "..." suffix if cut.
    """
    if not text:
        return None
    text = str(text)
    if len(text) <= max_length:
        return text
    truncated = text[:max_length]
    last_space = truncated.rfind(" ")
    if last_space > max_length * 0.8:
        truncated = truncated[:last_space]
    return truncated + "..."
