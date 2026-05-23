"""
Context Utilities - Helper functions for context window management.

Provides model name normalization, minimum safe context calculation, and metadata building.
"""
from typing import Dict, Any


def normalize_model_name(model_name: str) -> str:
    """Normalize model name by removing provider prefix if present."""
    return model_name.split("/")[-1] if "/" in model_name else model_name


def calculate_minimum_safe_context(model_context_window: int) -> int:
    """Calculate minimum safe context value (10% of window, minimum 1000)."""
    return max(1000, model_context_window // 10)


def build_adjustment_metadata(
    model_name: str,
    model_context_window: int,
    available_context: int,
    system_message_tokens: int,
    content_tokens: int,
    max_completion_tokens: int,
    truncated: bool = False,
    truncation_ratio: float = 0.0,
    adjusted_content_tokens: int = 0
) -> Dict[str, Any]:
    """Build metadata dictionary for content adjustment."""
    metadata = {
        "model": model_name,
        "model_context_window": model_context_window,
        "available_context": available_context,
        "system_tokens": system_message_tokens,
        "content_tokens": content_tokens,
        "total_tokens": system_message_tokens + content_tokens,
        "max_completion_tokens": max_completion_tokens,
        "truncated": truncated,
        "truncation_ratio": truncation_ratio,
    }
    if truncated:
        metadata["adjusted_content_tokens"] = adjusted_content_tokens
    return metadata
