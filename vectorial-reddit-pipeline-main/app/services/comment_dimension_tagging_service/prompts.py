"""Comment trait+dimension tagging prompts."""
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
    "comment_tait_dimension_tagging": """### ROLE
You are an assistant that identifies what a user reveals about themselves in a comment, using the surrounding discussion context to understand their perspective.

### INPUT
**Comment Context**: {context_summary} 
**User Comment**: {comment_body} 

### TASK
Determine whether the USER provides personal information related to the dimensions below. 
- **The Context** helps you interpret the comment.
- **Rule**: Only mark "yes" if the information reasonably applies to the USER. Do not tag information just because the topic is mentioned in the discussion.

**DIMENSIONS TO CHECK**:
{TAGGING_QUESTIONS_LIST}

For each trait below, answer "yes" if the comment shows evidence that the trait applies to the user. Answer "no" if it does not.

**TRAITS TO CHECK**:
{TRAIT_TAGGING_QUESTIONS_LIST}


### OUTPUT FORMAT (STRICT JSON ONLY)
Return ONLY a raw JSON object. Start with {{ and end with }}.
IMPORTANT: Keys must match the Dimension and Trait names exactly.

{{
  "dimensions": {DYNAMIC_TAGGING_SCHEMA},
  "traits": {DYNAMIC_TRAITS_SCHEMA}
}}""",
}
    
    # Keep old fallbacks for backward compatibility

prompt_service = PromptService(
    api_key=langsmith_api_key,
    cache_duration=600,
    fallback_prompts=FALLBACK_PROMPTS
)

# Single master prompt
comment_tait_dimension_tagging_prompt = CachedPrompt(
    "comment_tait_dimension_tagging",
    prompt_service
)

# Keep old prompts for backward compatibility (if needed)
comment_dimension_tagging_b2b_prompt = CachedPrompt("comment_dimension_tagging_b2b", prompt_service)
comment_dimension_tagging_b2c_prompt = CachedPrompt("comment_dimension_tagging_b2c", prompt_service)

__all__ = [
    "prompt_service",
    "comment_tait_dimension_tagging_prompt",
    "comment_dimension_tagging_b2b_prompt",
    "comment_dimension_tagging_b2c_prompt",
    "B2B_DIMENSIONS",
    "B2C_DIMENSIONS",
]