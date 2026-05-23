"""Summary generation coordinator - decides single vs multi-batch processing."""
from typing import List, Dict, Any, Optional
from fastapi import HTTPException
from app.config import logger
from app.services.ai_gateway_service import ai_gateway
from app.services.user_profile_summarization_service.multi_batch_orchestrator import BatchOrchestrator
from app.services.user_profile_summarization_service.single_batch_summary_processor import SingleBatchProcessor
from app.services.user_profile_summarization_service.utils.prompt_formatter import PromptProcessor
from app.services.user_profile_summarization_service.config import SummarizationConfig
from app.services.user_profile_summarization_service.utils.activity_utils import extract_activity_texts
from app.services.user_profile_summarization_service.utils.result_builder import ResultBuilder
from app.services.dynamic_context_window_management_service import context_manager


class ProfileSummaryGenerator:
    """Generates comprehensive summaries for Reddit profiles using map-reduce batching."""
    
    def __init__(self):
        """Initialize profile summary generator."""
        self.batch_orchestrator = BatchOrchestrator()
        self.single_batch_processor = SingleBatchProcessor()
        self.prompt_processor = PromptProcessor()
        self.config = SummarizationConfig()
    
    async def generate_from_activities(
        self,
        profile_id: str,
        profile_name: str,
        activities: List[Dict[str, Any]],
        model: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Generate comprehensive summary for a Reddit profile using map-reduce batching.
        """
        if not ai_gateway.enabled:
            raise HTTPException(
                status_code=503,
                detail="AI Gateway not enabled. Please configure AI_GATEWAY_API_KEY or OPENAI_API_KEY."
            )
        
        if not activities:
            return ResultBuilder.empty_summary_result()
        
        model = self.config.get_model(model)
        logger.info(f"Profile {profile_id}: Using model: {model}")
        
        activity_texts = extract_activity_texts(activities)
        if not activity_texts:
            return ResultBuilder.empty_summary_result()
        
        profile_context = profile_name
        
        # Prepare system message for batch calculation
        temp_system_message = self.prompt_processor.prepare_system_message_for_batching(
            profile_context=profile_context,
            total_activities=len(activity_texts)
        )
        
        batches, batch_metadata = context_manager.calculate_optimal_batch_sizes_for_activities(
            activity_text_list=activity_texts,
            system_message=temp_system_message,
            model_name=model,
            max_completion_tokens=self.config.max_completion_tokens_single,
            minimum_activities_per_batch=1
        )
        
        if not batches:
            logger.error(
                f"Profile {profile_id}: Failed to create batches - "
                f"{batch_metadata.get('error', 'unknown error')}"
            )
            return ResultBuilder.empty_summary_result()
        
        self._log_batch_info(profile_id, activity_texts, batches, batch_metadata)
        
        # Handle single batch case
        if len(batches) == 1:
            return await self.single_batch_processor.process(
                profile_id, profile_name, profile_context, batches[0], 
                activity_texts, model
            )
        
        # Handle multiple batches (map-reduce)
        return await self.batch_orchestrator.process_multiple_batches(
            profile_id, profile_name, profile_context, batches,
            batch_metadata, activity_texts, model
        )
    
    def _log_batch_info(
        self,
        profile_id: str,
        activity_texts: List[str],
        batches: List[List[str]],
        batch_metadata: Dict[str, Any]
    ) -> None:
        """Log batch information."""
        logger.info(
            f"Profile {profile_id}: Dynamic batching complete - "
            f"{len(activity_texts)} activities → {len(batches)} batches "
            f"(model: {batch_metadata.get('model', 'unknown')}, "
            f"context window: {batch_metadata.get('model_context_window', 0)} tokens)"
        )
        
        for stat in batch_metadata.get("batch_stats", []):
            logger.debug(
                f"Profile {profile_id}: Batch {stat['batch_index']} - "
                f"{stat['activities']} activities, {stat['tokens']} tokens "
                f"({stat['utilization']:.1%} utilization)"
            )
