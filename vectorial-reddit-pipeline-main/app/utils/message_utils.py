"""Shared message formatting utilities."""
from typing import Tuple


def split_prompt_into_messages(full_prompt: str) -> Tuple[str, str]:
    """Split a combined prompt (system + user) into separate system and user messages."""
    if "\n\n" not in full_prompt:
        return ("", full_prompt)
    
    parts = full_prompt.split("\n\n", 1)
    if len(parts) != 2:
        return ("", full_prompt)
    
    system_message = parts[0].strip()
    user_message = parts[1].strip()
    
    # Check if first part looks like a system message
    if not any(
        keyword in system_message.lower()
        for keyword in ["you are", "expert", "assistant"]
    ):
        return ("", full_prompt)
    
    return (system_message, user_message)
