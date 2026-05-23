"""Post trait+dimension tagging prompts."""
import os
from PromptService import PromptService, CachedPrompt

langsmith_api_key = os.getenv("LANGSMITH_API_KEY")

# B2B Dimensions - will be replaced by config
B2B_DIMENSIONS = [
    "Location",
    "Education",
    "Seniority",
    "Industry",
    "Company size",
    "Years of experience"
]

# B2C Dimensions - will be replaced by config
B2C_DIMENSIONS = [
    "Age band",
    "Income band",
    "Geography",
    "Life stage/Family type",
    "Gender",
    "Education"
]

# Master prompt service
FALLBACK_PROMPTS = {
    "post_trait_dimension_tagging": """### ROLE
You are a Content Intelligence Specialist. Your task is to analyze a Reddit Post and determine if the author reveals personal, verifiable information about themselves across specific dimensions and audience traits.

### INPUT
**Post Title**: {title}
**Post Content**: {content}

### TASK
For each dimension below, answer "yes" if the post contains information about the AUTHOR'S own life or status. Answer "no" if it does not. 

**DIMENSIONS TO CHECK**:
{TAGGING_QUESTIONS_LIST}

For each trait below, answer "yes" if the post content shows evidence that the trait applies to the author. Answer "no" if it does not.

**TRAITS TO CHECK**:
{TRAIT_TAGGING_QUESTIONS_LIST}

### OUTPUT FORMAT (STRICT JSON ONLY)
Return ONLY a raw JSON object. Start with {{ and end with }}.
IMPORTANT: Keys must match the Dimension and Trait names exactly.

{{
  "dimensions": {DYNAMIC_TAGGING_SCHEMA},
  "traits": {DYNAMIC_TRAITS_SCHEMA}
}}"""
}

prompt_service = PromptService(
    api_key=langsmith_api_key,
    cache_duration=600,
    fallback_prompts=FALLBACK_PROMPTS

)

# Single master prompt
post_trait_dimension_tagging_prompt = CachedPrompt(
    "post_trait_dimension_tagging",
    prompt_service
)

__all__ = [
    "prompt_service",
    "post_trait_dimension_tagging_prompt",
    "B2B_DIMENSIONS",
    "B2C_DIMENSIONS",
]
