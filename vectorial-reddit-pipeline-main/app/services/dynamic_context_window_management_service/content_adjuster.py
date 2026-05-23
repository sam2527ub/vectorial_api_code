"""
Content Adjuster - Handles content truncation, adjustment, and metadata.

Truncates content to fit within token limits using accurate token-based truncation. Builds
comprehensive metadata about adjustments (truncation ratios, token counts) for debugging.
"""
from typing import Dict, Any, Tuple
from app.config import logger
from app.services.dynamic_context_window_management_service.token_counter import TokenCounter
from app.services.dynamic_context_window_management_service.utils.context_utils import build_adjustment_metadata
from app.services.dynamic_context_window_management_service.model_resolver import ModelResolver


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
    
    def adjust_content(
        self,
        content: str,
        system_message: str,
        model_name: str,
        available_context: int,
        max_completion_tokens: int,
        model_resolver: ModelResolver
    ) -> Tuple[str, Dict[str, Any]]:
        """Adjust content to fit within available context window."""
        system_message_tokens = self.token_counter.count_tokens(system_message, model_name)
        content_tokens = self.token_counter.count_tokens(content, model_name)
        total_tokens = system_message_tokens + content_tokens
        
        model_context_window = model_resolver.get_context_window_size(model_name)
        adjustment_metadata = self.build_adjustment_metadata(
            model_name, model_context_window, available_context,
            system_message_tokens, content_tokens, max_completion_tokens
        )
        
        if total_tokens <= available_context:
            logger.debug(
                f"Content fits within context window: {total_tokens} tokens <= "
                f"{available_context} available (model: {model_name})"
            )
            return content, adjustment_metadata
        
        max_content_tokens = available_context - system_message_tokens
        
        if max_content_tokens <= 0:
            logger.error(
                f"System message ({system_message_tokens} tokens) exceeds available "
                f"context ({available_context} tokens) for model {model_name}. "
                f"Returning empty content."
            )
            return "", {
                **adjustment_metadata, 
                "truncated": True, 
                "error": "system_message_too_large"
            }
        
        logger.warning(
            f"Content exceeds context window ({total_tokens} > {available_context}). "
            f"Truncating content from {content_tokens} to {max_content_tokens} tokens "
            f"(model: {model_name})"
        )
        
        truncated_content, truncation_metadata = self.truncate_content(
            content, max_content_tokens, model_name, content_tokens
        )
        adjustment_metadata.update(truncation_metadata)
        
        return truncated_content, adjustment_metadata