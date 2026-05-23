"""
Batch Calculator - Creates optimal batches of activities based on token limits.

Groups activities into batches that fit within available context tokens. Calculates batch
statistics and builds metadata for tracking and debugging.
"""
from typing import List, Dict, Any, Tuple
from app.config import logger
from app.services.dynamic_context_window_management_service.token_counter import TokenCounter
from app.services.dynamic_context_window_management_service.config import ContextConfig
from app.services.dynamic_context_window_management_service.model_resolver import ModelResolver


class BatchCalculator:
    """Handles batch creation, statistics calculation, and optimization."""
    
    def __init__(self, token_counter: TokenCounter, config: ContextConfig):
        self.token_counter = token_counter
        self.config = config
    
    def create_batches(
        self,
        activity_text_list: List[str],
        available_for_content: int,
        model_name: str
    ) -> List[List[str]]:
        """Create batches from activity list based on token limits."""
        batches_list = []
        current_batch = []
        current_batch_tokens = 0
        
        for activity_text in activity_text_list:
            activity_tokens = self.token_counter.count_tokens(activity_text, model_name)
            
            if current_batch and (
                current_batch_tokens + activity_tokens > available_for_content
            ):
                batches_list.append(current_batch)
                current_batch = [activity_text]
                current_batch_tokens = activity_tokens
            else:
                current_batch.append(activity_text)
                current_batch_tokens += activity_tokens
        
        if current_batch:
            batches_list.append(current_batch)
        
        return batches_list
    
    def calculate_batch_statistics(
        self,
        batches_list: List[List[str]],
        available_for_content: int,
        model_name: str
    ) -> List[Dict[str, Any]]:
        """Calculate statistics for each batch."""
        batch_statistics = []
        for batch_index, batch in enumerate(batches_list):
            batch_total_tokens = sum(
                self.token_counter.count_tokens(text, model_name) 
                for text in batch
            )
            batch_utilization = (
                batch_total_tokens / available_for_content 
                if available_for_content > 0 
                else 0
            )
            batch_statistics.append({
                "batch_index": batch_index + 1,
                "activities": len(batch),
                "tokens": batch_total_tokens,
                "utilization": batch_utilization,
            })
        return batch_statistics
    
    def build_batch_metadata(
        self,
        batches_list: List[List[str]],
        batch_statistics: List[Dict[str, Any]],
        model_name: str,
        model_context_window: int,
        available_context: int,
        available_for_content: int,
        system_message_tokens: int,
        total_activities_count: int,
        total_tokens_count: int,
        max_completion_tokens: int
    ) -> Dict[str, Any]:
        """Build comprehensive batch metadata dictionary."""
        return {
            "model": model_name,
            "model_context_window": model_context_window,
            "available_context": available_context,
            "available_for_content": available_for_content,
            "system_tokens": system_message_tokens,
            "total_activities": total_activities_count,
            "total_content_tokens": total_tokens_count,
            "total_batches": len(batches_list),
            "max_completion_tokens": max_completion_tokens,
            "batch_stats": batch_statistics,
            "avg_batch_size": (
                total_activities_count / len(batches_list) 
                if batches_list 
                else 0
            ),
            "avg_batch_tokens": (
                total_tokens_count / len(batches_list) 
                if batches_list 
                else 0
            ),
        }
    
    def calculate_optimal_batches(
        self,
        activity_text_list: List[str],
        system_message: str,
        model_name: str,
        available_context: int,
        max_completion_tokens: int,
        model_context_window: int,
        model_resolver: ModelResolver
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
        
        batches_list = self.create_batches(
            activity_text_list, available_for_content, model_name
        )
        
        batch_statistics = self.calculate_batch_statistics(
            batches_list, available_for_content, model_name
        )
        
        batch_metadata = self.build_batch_metadata(
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