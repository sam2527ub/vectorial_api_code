"""Shared message formatting utilities (vectorial-reddit-pipeline style)."""
from typing import Tuple, Optional


def split_prompt_into_messages(
    full_prompt: str,
    default_system_message: Optional[str] = None,
) -> Tuple[str, str]:
    """Split combined prompt (system + user) into system and user messages."""
    default = default_system_message or (
        "You are an expert at analyzing LinkedIn posts and generating professional summaries. "
        "Always respond with valid JSON only."
    )
    if "\n\n" not in full_prompt:
        return (default, full_prompt)
    parts = full_prompt.split("\n\n", 1)
    system_message = parts[0].strip()
    user_message = parts[1].strip() if len(parts) > 1 else full_prompt
    if not any(kw in system_message.lower() for kw in ("you are", "expert", "assistant")):
        return (default, full_prompt)
    return (system_message, user_message)
