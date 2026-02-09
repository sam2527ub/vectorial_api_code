"""
AI Gateway Utilities - Helper functions for message formatting and model name normalization.

This file provides utility functions used across the AI Gateway module:
- Model name normalization (adds provider prefixes for Vercel gateway)
- Message formatting for Claude API (extracts system messages, formats user/assistant messages)
- Model provider detection (identifies which provider a model belongs to)
These utilities ensure consistent message and model name formatting across different providers.
"""
from typing import List, Dict, Any, Optional, Tuple


# Model Provider Detection

def get_model_provider(model: str) -> str:
    """Extract provider from model name. Returns 'openai', 'anthropic', 'groq', or 'unknown'."""
    if "/" in model:
        provider = model.split("/")[0].lower()
        if provider in ["openai", "anthropic", "groq", "google", "vertex", "bedrock"]:
            return provider
    
    # Detect provider by model name pattern if no prefix
    model_lower = model.lower()
    if model_lower.startswith("claude"):
        return "anthropic"
    elif model_lower.startswith("gpt") or model_lower.startswith("o1"):
        return "openai"
    elif model_lower.startswith("llama") or model_lower.startswith("mixtral") or model_lower.startswith("gemma"):
        return "groq"
    
    # Default to openai if no provider prefix (for backward compatibility)
    return "openai"


def normalize_model_name(model: str, provider: str) -> str:
    """Normalize model name. Vercel adds provider prefix if missing, others return as-is."""
    if provider == "vercel" and "/" not in model:
        detected_provider = get_model_provider(model)
        return f"{detected_provider}/{model}"
    return model


def prepare_claude_messages(messages: List[Dict[str, str]]) -> Tuple[Optional[str], List[Dict[str, str]]]:
    """Extract system message and return (system_message, user/assistant_messages)."""
    system_message = None
    claude_messages = []
    
    for msg in messages:
        if msg.get("role") == "system":
            system_message = msg.get("content", "")
        elif msg.get("role") in ["user", "assistant"]:
            claude_messages.append({
                "role": msg.get("role"),
                "content": msg.get("content", "")
            })
    
    return system_message, claude_messages
