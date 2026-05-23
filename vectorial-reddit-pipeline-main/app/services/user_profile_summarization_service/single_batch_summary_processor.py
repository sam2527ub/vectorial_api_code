"""Single batch summary processing for profiles with small activity counts."""
from typing import List, Dict, Any
from app.config import logger
from app.services.ai_gateway_service import ai_gateway
from app.services.user_profile_summarization_service.utils.prompt_formatter import PromptProcessor
from app.services.user_profile_summarization_service.config import SummarizationConfig
from app.services.user_profile_summarization_service.utils.result_builder import ResultBuilder


class SingleBatchProcessor:
    """Handles single batch summary generation."""
    
    def __init__(self):
        """Initialize single batch processor."""
        self.config = SummarizationConfig()
        self.prompt_processor = PromptProcessor()
    
    async def process(
        self,
        profile_id: str,
        profile_name: str,
        profile_context: str,
        batch: List[str],
        activity_texts: List[str],
        model: str
    ) -> Dict[str, Any]:
        """Process single batch summary."""
        logger.info(f"Profile {profile_id}: Single batch detected - using final summary prompt")
        
        text_for_analysis = "\n\n".join(batch)
        system_message, user_prompt = self.prompt_processor.format_single_batch_prompt(
            profile_context=profile_context,
            total_activities=len(activity_texts),
            text_for_analysis=text_for_analysis,
            profile_name=profile_name
        )
        
        try:
            result = await ai_gateway.call_via_gateway(
                context_id=profile_id,
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": user_prompt}
                ],
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
                "highlights": result.get("highlights", []),
                "keywords": result.get("keywords", [])
            }
        except Exception as e:
            logger.error(f"Error generating summary for single batch profile {profile_id}: {e}")
            return ResultBuilder.empty_summary_result()
