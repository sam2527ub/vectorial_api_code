"""Content adjustment handler for context window management."""
from typing import Dict, Any, Tuple
from app.config import logger
from app.services.dynamic_context_window_management_service.token_counter import TokenCounter
from app.services.dynamic_context_window_management_service.content_truncator import ContentAdjuster
from app.services.dynamic_context_window_management_service.model_resolver import ModelResolver


class ContentAdjustmentHandler:
    """Handles content adjustment to fit within context windows."""
    
    def __init__(
        self,
        token_counter: TokenCounter,
        content_adjuster: ContentAdjuster,
        model_resolver: ModelResolver
    ):
        """Initialize content adjustment handler."""
        self.token_counter = token_counter
        self.content_adjuster = content_adjuster
        self.model_resolver = model_resolver
    
    def adjust_content(
        self,
        content: str,
        system_message: str,
        model_name: str,
        available_context: int,
        max_completion_tokens: int
    ) -> Tuple[str, Dict[str, Any]]:
        """Adjust content to fit within available context window."""
        system_message_tokens = self.token_counter.count_tokens(system_message, model_name)
        content_tokens = self.token_counter.count_tokens(content, model_name)
        total_tokens = system_message_tokens + content_tokens
        
        model_context_window = self.model_resolver.get_context_window_size(model_name)
        adjustment_metadata = self.content_adjuster.build_adjustment_metadata(
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
        
        truncated_content, truncation_metadata = self.content_adjuster.truncate_content(
            content, max_content_tokens, model_name, content_tokens
        )
        adjustment_metadata.update(truncation_metadata)
        
        return truncated_content, adjustment_metadata
