"""Intermediate batch processing for map phase of map-reduce pattern."""
from typing import List, Dict, Any, Optional
from app.config import logger
from app.services.ai_gateway_service import ai_gateway
from app.services.user_profile_summarization_service.utils.prompt_formatter import PromptProcessor
from app.services.user_profile_summarization_service.config import SummarizationConfig
from app.services.dynamic_context_window_management_service import context_manager


class BatchProcessor:
    """Handles batch summary generation for activities."""
    
    def __init__(self):
        """Initialize batch processor."""
        self.config = SummarizationConfig()
        self.prompt_processor = PromptProcessor()
    
    async def generate_batch_summary(
        self,
        profile_id: str,
        profile_name: str,
        profile_context: str,
        activity_texts: List[str],
        total_activities: int,
        is_final: bool = True,
        model: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Generate summary for a single batch of activities.
        """
        model = self.config.get_model(model)
        text_for_analysis = "\n\n".join(activity_texts)
        
        system_message, user_prompt = self.prompt_processor.format_batch_summary_prompt(
            profile_context=profile_context,
            total_activities=total_activities,
            text_for_analysis=text_for_analysis
        )
        
        max_completion_tokens = (
            self.config.max_completion_tokens_single 
            if is_final 
            else self.config.max_completion_tokens_batch
        )
        
        adjusted_user_prompt, adjust_metadata = context_manager.adjust_content_to_fit_context_window(
            content=user_prompt,
            system_message=system_message,
            model_name=model,
            max_completion_tokens=max_completion_tokens
        )
        
        if adjust_metadata.get("truncated"):
            logger.warning(
                f"Profile {profile_id}: Batch content truncated "
                f"({adjust_metadata.get('truncation_ratio', 0):.1%} reduction) "
                f"to fit {model} context window"
            )
        
        try:
            result = await ai_gateway.call_via_gateway(
                context_id=profile_id,
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": adjusted_user_prompt}
                ],
                max_tokens=max_completion_tokens,
                model=model,
                default_model=self.config.default_model,
                fallback_models=self.config.fallback_models,
                config_default_attr='profile_summary_default',
                config_fallbacks_attr='profile_summary_fallbacks',
                hardcoded_default="openai/gpt-5-mini",
                validate_summary=False,
                return_text=False,
                direct_api_fallback_model=model or self.config.default_model or "gpt-5-mini"
            )
            
            return {
                "summary": result.get("summary", ""),
                "highlights": result.get("highlights", []),
                "keywords": result.get("keywords", [])
            }
        except Exception as e:
            logger.error(f"Error generating batch summary for profile {profile_id}: {e}")
            return {
                "summary": None,
                "highlights": [],
                "keywords": []
            }
