"""Traits generation using Claude."""
from typing import List, Dict, Any
from app.config import logger
from app.services.ai_gateway_service import ai_gateway
from app.services.audience_group_summarization_service.config import GroupSummaryConfig
from app.utils.message_utils import split_prompt_into_messages
from app.services.audience_group_summarization_service.utils.json_parser import extract_and_parse_json
from app.services.audience_group_summarization_service.prompts import reddit_traits_generation_prompt
from app.services.audience_group_summarization_service.utils.profile_summary_utils import combine_profile_summaries


class TraitsGenerator:
    """Generates traits from profile summaries."""
    
    def __init__(self):
        """Initialize traits generator."""
        self.config = GroupSummaryConfig()
    
    async def generate(
        self,
        profile_summaries: List[Dict[str, str]],
        audience_room_id: str
    ) -> List[Dict[str, Any]]:
        """
        Generate traits from profile summaries.
        """
        # Combine all profile summaries
        combined_text = combine_profile_summaries(profile_summaries)
        
        # Use LangSmith prompt for traits generation
        full_traits_prompt = reddit_traits_generation_prompt.format(
            total_profiles=len(profile_summaries),
            combined_profile_summaries_text=combined_text
        )
        
        # Split prompt into system and user messages
        traits_system_message, traits_user_prompt = split_prompt_into_messages(
            full_traits_prompt
        )
        
        # Generate traits using Claude Sonnet
        traits_messages = []
        if traits_system_message:
            traits_messages.append({"role": "system", "content": traits_system_message})
        traits_messages.append({"role": "user", "content": traits_user_prompt})
        
        def handle_legacy_model(m, default):
            if m == "claude-sonnet-4-5-20250929":
                return None
            return m
        
        result = await ai_gateway.call_via_gateway(
            context_id=audience_room_id,
            messages=traits_messages,
            max_tokens=self.config.traits_max_tokens,
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
        
        traits_response = result if isinstance(result, str) else str(result)
        
        # Extract and parse JSON from response
        try:
            traits_data = extract_and_parse_json(
                traits_response,
                max_log_length=self.config.max_log_length
            )
        except ValueError as e:
            logger.error(f"Failed to extract JSON from traits response: {e}")
            # Log the full response for debugging (truncated)
            logger.error(
                f"Traits response (first {self.config.max_log_length} chars): "
                f"{traits_response[:self.config.max_log_length]}"
            )
            if len(traits_response) > self.config.max_log_length:
                logger.error(f"Traits response (last 500 chars): {traits_response[-500:]}")
            raise ValueError(f"Failed to parse traits JSON: {str(e)}")
        
        # Validate that we have the expected structure
        if not isinstance(traits_data, dict) or "traits" not in traits_data:
            raise ValueError("Invalid traits JSON structure: missing 'traits' key")
        
        traits_list = traits_data.get("traits", [])
        if not isinstance(traits_list, list) or len(traits_list) == 0:
            raise ValueError(
                f"Invalid traits JSON structure: expected at least 1 trait, "
                f"got {len(traits_list)}"
            )
        
        # Log what we got (for debugging if prompt changes)
        logger.info(f"Generated {len(traits_list)} traits from prompt")
        
        # Validate each trait has the required fields (structure validation only)
        self._validate_traits(traits_list)
        
        logger.info(
            f"Successfully generated and validated {len(traits_list)} traits "
            f"for audience room {audience_room_id}"
        )
        
        return traits_list
    
    def _validate_traits(self, traits_list: List[Dict[str, Any]]) -> None:
        """Validate traits structure."""
        for idx, trait in enumerate(traits_list):
            if not isinstance(trait, dict):
                raise ValueError(f"Trait at index {idx} is not a valid object")
            
            if "title" not in trait:
                raise ValueError(f"Trait at index {idx} missing required field 'title'")
            
            if "keywordTags" not in trait or "descriptions" not in trait:
                raise ValueError(
                    f"Trait '{trait.get('title', f'at index {idx}')}' "
                    f"missing required fields 'keywordTags' or 'descriptions'"
                )
            
            if not isinstance(trait["keywordTags"], list) or not isinstance(
                trait["descriptions"], list
            ):
                raise ValueError(
                    f"Trait '{trait.get('title')}' has invalid keywordTags or "
                    f"descriptions format (must be arrays)"
                )
            
            if len(trait["keywordTags"]) != len(trait["descriptions"]):
                raise ValueError(
                    f"Trait '{trait.get('title')}' has mismatched keywordTags and "
                    f"descriptions counts ({len(trait['keywordTags'])} vs "
                    f"{len(trait['descriptions'])})"
                )
