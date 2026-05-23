"""Multi-batch orchestration logic for map-reduce pattern."""
import asyncio
from typing import List, Dict, Any
from app.config import logger
from app.services.user_profile_summarization_service.intermediate_batch_processor import BatchProcessor
from app.services.user_profile_summarization_service.batch_summary_combiner import BatchCombiner
from app.services.user_profile_summarization_service.config import SummarizationConfig
from app.services.user_profile_summarization_service.utils.result_builder import ResultBuilder


class BatchOrchestrator:
    """Orchestrates batch processing using map-reduce pattern."""
    
    def __init__(self):
        """Initialize batch orchestrator."""
        self.batch_processor = BatchProcessor()
        self.batch_combiner = BatchCombiner()
        self.config = SummarizationConfig()
    
    async def process_multiple_batches(
        self,
        profile_id: str,
        profile_name: str,
        profile_context: str,
        batches: List[List[str]],
        batch_metadata: Dict[str, Any],
        activity_texts: List[str],
        model: str
    ) -> Dict[str, Any]:
        """
        Process multiple batches using map-reduce pattern.
        """
        # Map phase: Generate summaries for each batch
        batch_summaries, all_keywords, all_highlights = await self._map_phase(
            profile_id, profile_name, profile_context, batches, batch_metadata, model
        )
        
        if not batch_summaries:
            logger.error(f"Profile {profile_id}: All batches failed - no summaries to combine")
            return ResultBuilder.empty_summary_result()
        
        # Reduce phase: Combine batch summaries
        return await self.batch_combiner.combine_batch_summaries(
            profile_id, profile_name, profile_context, batch_summaries,
            all_keywords, all_highlights, batches, activity_texts, model
        )
    
    async def _map_phase(
        self,
        profile_id: str,
        profile_name: str,
        profile_context: str,
        batches: List[List[str]],
        batch_metadata: Dict[str, Any],
        model: str
    ) -> tuple:
        """Map phase: Process each batch and collect summaries."""
        batch_summaries = []
        all_keywords = []
        all_highlights = []
        
        for idx, batch in enumerate(batches):
            logger.info(
                f"Profile {profile_id}: Processing batch {idx + 1}/{len(batches)} "
                f"({len(batch)} activities, ~{batch_metadata['batch_stats'][idx]['tokens']} tokens)"
            )
            
            batch_result = await self.batch_processor.generate_batch_summary(
                profile_id, profile_name, profile_context, batch, len(batch),
                is_final=False, model=model
            )
            
            if batch_result.get("summary"):
                batch_summaries.append(batch_result["summary"])
            if batch_result.get("keywords"):
                all_keywords.extend(batch_result["keywords"])
            if batch_result.get("highlights"):
                all_highlights.extend(batch_result["highlights"])
            
            await asyncio.sleep(self.config.batch_delay_seconds)
        
        return batch_summaries, all_keywords, all_highlights
