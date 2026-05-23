"""Batch summary combination logic for reduce phase of map-reduce pattern."""
from typing import List, Dict, Any
from app.config import logger
from app.services.ai_gateway_service import ai_gateway
from app.services.user_profile_summarization_service.utils.prompt_formatter import PromptProcessor
from app.services.user_profile_summarization_service.config import SummarizationConfig
from app.services.dynamic_context_window_management_service import context_manager


class BatchCombiner:
    """Combines multiple batch summaries into final summary."""
    
    def __init__(self):
        """Initialize batch combiner."""
        self.config = SummarizationConfig()
        self.prompt_processor = PromptProcessor()
    
    async def combine_batch_summaries(
        self,
        profile_id: str,
        profile_name: str,
        profile_context: str,
        batch_summaries: List[str],
        all_keywords: List[str],
        all_highlights: List[str],
        batches: List[List[str]],
        activity_texts: List[str],
        model: str
    ) -> Dict[str, Any]:
        """
        Combine multiple batch summaries into final summary.
        """
        unique_keywords = list(dict.fromkeys(all_keywords))[:self.config.max_keywords]
        unique_highlights = list(dict.fromkeys(all_highlights))[:self.config.max_highlights]
        
        combined_batch_summaries_text = "\n\n".join(
            [f"Batch {i+1}: {s}" for i, s in enumerate(batch_summaries)]
        )
        
        system_message, user_prompt = self.prompt_processor.format_combine_summaries_prompt(
            profile_context=profile_context,
            total_activities=len(activity_texts),
            batch_count=len(batches),
            combined_batch_summaries_text=combined_batch_summaries_text,
            profile_name=profile_name
        )
        
        adjusted_user_prompt, adjust_metadata = context_manager.adjust_content_to_fit_context_window(
            content=user_prompt,
            system_message=system_message,
            model_name=model,
            max_completion_tokens=self.config.max_completion_tokens_single
        )
        
        if adjust_metadata.get("truncated"):
            logger.warning(
                f"Profile {profile_id}: Combined summaries truncated "
                f"({adjust_metadata.get('truncation_ratio', 0):.1%} reduction)"
            )
        
        try:
            messages = []
            if system_message:
                messages.append({"role": "system", "content": system_message})
            messages.append({"role": "user", "content": adjusted_user_prompt})
            
            result = await ai_gateway.call_via_gateway(
                context_id=profile_id,
                messages=messages,
                max_tokens=self.config.max_completion_tokens_single,
                model=model,
                default_model=self.config.default_model,
                fallback_models=self.config.fallback_models,
                config_default_attr='profile_summary_default',
                config_fallbacks_attr='profile_summary_fallbacks',
                hardcoded_default="openai/gpt-5-mini",
                validate_summary=True,
                return_text=False,
                direct_api_fallback_model=model or self.config.default_model or "gpt-5-mini"
            )
            
            return {
                "summary": result.get("summary", ""),
                "highlights": result.get("highlights", unique_highlights),
                "keywords": result.get("keywords", unique_keywords)
            }
        except Exception as e:
            logger.error(f"Error combining batch summaries for profile {profile_id}: {e}")
            fallback_summary = batch_summaries[0] if batch_summaries else None
            return {
                "summary": fallback_summary,
                "highlights": unique_highlights,
                "keywords": unique_keywords
            }
