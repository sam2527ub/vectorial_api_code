"""Batch optimization handler for context window management."""
from typing import List, Dict, Any, Tuple
from app.config import logger
from app.services.dynamic_context_window_management_service.token_counter import TokenCounter
from app.services.dynamic_context_window_management_service.activity_batch_calculator import BatchCalculator
from app.services.dynamic_context_window_management_service.config import ContextConfig
from app.services.dynamic_context_window_management_service.model_resolver import ModelResolver


class BatchOptimizationHandler:
    """Handles optimal batch size calculation for activities."""
    
    def __init__(
        self,
        token_counter: TokenCounter,
        batch_calculator: BatchCalculator,
        model_resolver: ModelResolver,
        config: ContextConfig
    ):
        """Initialize batch optimization handler."""
        self.token_counter = token_counter
        self.batch_calculator = batch_calculator
        self.model_resolver = model_resolver
        self.config = config
    
    def calculate_optimal_batches(
        self,
        activity_text_list: List[str],
        system_message: str,
        model_name: str,
        available_context: int,
        max_completion_tokens: int,
        model_context_window: int
    ) -> Tuple[List[List[str]], Dict[str, Any]]:
        """Calculate optimal batch sizes based on actual token counts."""
        system_message_tokens = self.token_counter.count_tokens(system_message, model_name)
        available_for_content = available_context - system_message_tokens
        
        if available_for_content <= 0:
            logger.error(
                f"System message ({system_message_tokens} tokens) exceeds available "
                f"context ({available_context} tokens) for model {model_name}. "
                f"Cannot create batches."
            )
            return [], {
                "error": "system_message_too_large",
                "system_tokens": system_message_tokens,
                "available_context": available_context,
            }
        
        total_tokens_count = sum(
            self.token_counter.count_tokens(text, model_name) 
            for text in activity_text_list
        )
        total_activities_count = len(activity_text_list)
        
        batches_list = self.batch_calculator.create_batches(
            activity_text_list, available_for_content, model_name
        )
        
        batch_statistics = self.batch_calculator.calculate_batch_statistics(
            batches_list, available_for_content, model_name
        )
        
        batch_metadata = self.batch_calculator.build_batch_metadata(
            batches_list, batch_statistics, model_name,
            model_context_window, available_context, available_for_content,
            system_message_tokens, total_activities_count,
            total_tokens_count, max_completion_tokens
        )
        
        if self.config.log_batch_statistics:
            logger.info(
                f"Dynamic batching for model {model_name}: "
                f"{total_activities_count} activities → {len(batches_list)} batches "
                f"({total_tokens_count} total tokens, "
                f"{available_for_content} available per batch)"
            )
        
        return batches_list, batch_metadata
