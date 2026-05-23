"""
Dynamic Context Manager - Main orchestrator for managing AI model context windows.

Main entry point for context window management. Orchestrates content adjustment, batch size
calculation, token counting, and metadata generation to ensure content fits within model limits.
"""
from typing import List, Dict, Any, Optional, Tuple
from app.services.dynamic_context_window_management_service.config import ContextConfig
from app.services.dynamic_context_window_management_service.token_counter import TokenCounter
from app.services.dynamic_context_window_management_service.batch_calculator import BatchCalculator
from app.services.dynamic_context_window_management_service.content_adjuster import ContentAdjuster
from app.services.dynamic_context_window_management_service.model_resolver import ModelResolver
from app.services.dynamic_context_window_management_service.context_calculator import ContextCalculator


class DynamicContextManager:
    """Manages dynamic context window adjustment for AI model requests."""
    
    def __init__(
        self, 
        safety_margin: Optional[int] = None, 
        config: Optional[ContextConfig] = None
    ):
        """Initialize the dynamic context manager."""
        self.config = config or ContextConfig()
        self.safety_margin = (
            safety_margin 
            if safety_margin is not None 
            else self.config.safety_margin
        )
        self.token_counter = TokenCounter(self.config)
        self.model_resolver = ModelResolver(self.config)
        self.context_calculator = ContextCalculator(
            self.model_resolver, 
            self.safety_margin
        )
        self.batch_calculator = BatchCalculator(self.token_counter, self.config)
        self.content_adjuster = ContentAdjuster(self.token_counter)
    
    def get_context_window_size_for_model(self, model_name: str) -> int:
        """Get the context window size (in tokens) for a specific model."""
        return self.model_resolver.get_context_window_size(model_name)
    
    def calculate_available_context_for_input(
        self,
        model_name: str,
        max_completion_tokens: int,
        safety_margin: Optional[int] = None
    ) -> int:
        """Calculate available context tokens for input after accounting for response and safety margin."""
        return self.context_calculator.calculate_available_context(
            model_name, max_completion_tokens, safety_margin
        )
    
    def adjust_content_to_fit_context_window(
        self,
        content: str,
        system_message: str,
        model_name: str,
        max_completion_tokens: int,
        safety_margin: Optional[int] = None
    ) -> Tuple[str, Dict[str, Any]]:
        """Dynamically adjust content to fit within model's context window."""
        available_context = self.calculate_available_context_for_input(
            model_name, max_completion_tokens, safety_margin
        )
        return self.content_adjuster.adjust_content(
            content, system_message, model_name, available_context, max_completion_tokens, self.model_resolver
        )
    
    def calculate_optimal_batch_sizes_for_activities(
        self,
        activity_text_list: List[str],
        system_message: str,
        model_name: str,
        max_completion_tokens: int,
        minimum_activities_per_batch: int = 1,
        safety_margin: Optional[int] = None
    ) -> Tuple[List[List[str]], Dict[str, Any]]:
        """Dynamically calculate optimal batch sizes based on actual token counts."""
        available_context = self.calculate_available_context_for_input(
            model_name, max_completion_tokens, safety_margin
        )
        model_context_window = self.get_context_window_size_for_model(model_name)
        
        return self.batch_calculator.calculate_optimal_batches(
            activity_text_list, system_message, model_name,
            available_context, max_completion_tokens, model_context_window, self.model_resolver
        )
