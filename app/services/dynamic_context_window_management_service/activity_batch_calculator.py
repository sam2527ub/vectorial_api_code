"""
Batch Calculator - Creates optimal batches of activities based on token limits.

Groups activities into batches that fit within available context tokens. Calculates batch
statistics and builds metadata for tracking and debugging.
"""
from typing import List, Dict, Any
from app.config import logger
from app.services.dynamic_context_window_management_service.token_counter import TokenCounter


class BatchCalculator:
    """Handles batch creation and statistics calculation."""
    
    def __init__(self, token_counter: TokenCounter, config):
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
