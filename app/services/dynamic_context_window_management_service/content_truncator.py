"""
Content Adjuster - Handles content truncation and adjustment metadata.

Truncates content to fit within token limits using accurate token-based truncation. Builds
comprehensive metadata about adjustments (truncation ratios, token counts) for debugging.
"""
from typing import Dict, Any, Tuple
from app.config import logger
from app.services.dynamic_context_window_management_service.token_counter import TokenCounter
from app.services.dynamic_context_window_management_service.utils.context_utils import build_adjustment_metadata


class ContentAdjuster:
    """Handles content adjustment and truncation for context windows."""
    
    def __init__(self, token_counter: TokenCounter):
        self.token_counter = token_counter
    
    def build_adjustment_metadata(
        self,
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
        return build_adjustment_metadata(
            model_name=model_name,
            model_context_window=model_context_window,
            available_context=available_context,
            system_message_tokens=system_message_tokens,
            content_tokens=content_tokens,
            max_completion_tokens=max_completion_tokens,
            truncated=truncated,
            truncation_ratio=truncation_ratio,
            adjusted_content_tokens=adjusted_content_tokens
        )
    
    def truncate_content(
        self,
        content: str,
        max_content_tokens: int,
        model_name: str,
        original_content_tokens: int
    ) -> Tuple[str, Dict[str, Any]]:
        """Truncate content and return with metadata."""
        truncated_content = self.token_counter.truncate_to_tokens(
            content, max_content_tokens, model_name
        )
        truncated_content_tokens = self.token_counter.count_tokens(truncated_content, model_name)
        
        truncation_metadata = {
            "truncated": True,
            "truncation_ratio": (
                1.0 - (truncated_content_tokens / original_content_tokens) 
                if original_content_tokens > 0 
                else 0.0
            ),
            "adjusted_content_tokens": truncated_content_tokens,
        }
        
        return truncated_content, truncation_metadata
