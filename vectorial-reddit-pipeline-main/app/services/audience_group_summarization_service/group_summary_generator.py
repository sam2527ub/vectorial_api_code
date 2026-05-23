"""Group summary generation using Claude."""
from typing import List, Dict
from app.config import logger
from app.services.ai_gateway_service import ai_gateway
from app.services.audience_group_summarization_service.config import GroupSummaryConfig
from app.utils.message_utils import split_prompt_into_messages
from app.services.audience_group_summarization_service.prompts import reddit_group_summary_prompt
from app.services.audience_group_summarization_service.utils.profile_summary_utils import combine_profile_summaries


class GroupSummaryGenerator:
    """Generates group summaries from profile summaries."""
    
    def __init__(self):
        """Initialize group summary generator."""
        self.config = GroupSummaryConfig()
    
    async def generate(
        self,
        profile_summaries: List[Dict[str, str]],
        audience_room_id: str
    ) -> str:
        """Generate group summary from profile summaries."""
        # Combine all profile summaries
        combined_text = combine_profile_summaries(profile_summaries)
        
        # Use LangSmith prompt for group summary
        full_group_prompt = reddit_group_summary_prompt.format(
            total_profiles=len(profile_summaries),
            combined_profile_summaries_text=combined_text
        )
        
        # Split prompt into system and user messages
        system_message, user_prompt = split_prompt_into_messages(full_group_prompt)
        
        # Generate group summary using Claude Sonnet
        messages = []
        if system_message:
            messages.append({"role": "system", "content": system_message})
        messages.append({"role": "user", "content": user_prompt})
        
        def handle_legacy_model(m, default):
            if m == "claude-sonnet-4-5-20250929":
                return None
            return m
        
        result = await ai_gateway.call_via_gateway(
            context_id=audience_room_id,
            messages=messages,
            max_tokens=self.config.group_summary_max_tokens,
            model=None,
            default_model=self.config.default_model,
            fallback_models=self.config.fallback_models,
            config_default_attr='group_summary_default',
            config_fallbacks_attr='group_summary_fallbacks',
            hardcoded_default="anthropic/claude-sonnet-4.5",
            validate_summary=False,
            return_text=True,
            legacy_model_handler=handle_legacy_model,
            direct_api_fallback_model=self.config.default_model or "claude-sonnet-4-5-20250929"
        )
        
        group_summary = result if isinstance(result, str) else str(result)
        
        return group_summary.strip()
