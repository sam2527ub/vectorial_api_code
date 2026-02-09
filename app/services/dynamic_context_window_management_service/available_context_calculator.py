"""
Context Calculator - Calculates available context tokens for AI model inputs.

Calculates available tokens for input after accounting for model context window, completion
tokens, and safety margin. Ensures non-negative values with minimum safe fallback.
"""
from typing import Optional
from app.config import logger
from app.services.dynamic_context_window_management_service.model_resolver import ModelResolver
from app.services.dynamic_context_window_management_service.utils.context_utils import calculate_minimum_safe_context


class ContextCalculator:
    """Handles context window calculations."""
    
    def __init__(self, model_resolver: ModelResolver, safety_margin: int):
        self.model_resolver = model_resolver
        self.safety_margin = safety_margin
    
    def calculate_available_context(
        self,
        model_name: str,
        max_completion_tokens: int,
        safety_margin: Optional[int] = None
    ) -> int:
        """Calculate available context tokens for input after accounting for response and safety margin."""
        model_context_window = self.model_resolver.get_context_window_size(model_name)
        safety_margin_to_use = (
            safety_margin 
            if safety_margin is not None 
            else self.safety_margin
        )
        
        available_context = (
            model_context_window 
            - max_completion_tokens 
            - safety_margin_to_use
        )
        
        if available_context < 0:
            logger.warning(
                f"Available context is negative for model {model_name}: "
                f"window={model_context_window}, "
                f"completion={max_completion_tokens}, "
                f"margin={safety_margin_to_use}. "
                f"Using minimum safe value."
            )
            return calculate_minimum_safe_context(model_context_window)
        
        return available_context
