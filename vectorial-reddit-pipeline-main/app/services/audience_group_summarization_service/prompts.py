"""Audience group summarization prompts loaded from LangSmith."""
import os
from PromptService import PromptService, CachedPrompt

langsmith_api_key = os.getenv("LANGSMITH_API_KEY")

FALLBACK_PROMPTS = {
    "reddit_group_summary_prompt": """You are an expert at analyzing groups of Reddit user profiles and generating comprehensive, insightful high-level summaries. Write detailed, informative summaries that capture collective patterns and insights.

Analyze the following group of {total_profiles} Reddit user profiles.

Individual Profile Summaries:
{combined_profile_summaries_text}

Generate a comprehensive high-level summary (6-10 sentences) that covers:
1. Overall themes and patterns across all profiles in this group
2. Common topics, technologies, or expertise areas shared among them
3. Community engagement patterns and subreddit participation characteristics
4. Professional focus areas (e.g., technical depth, thought leadership, product development, community building)
5. Industry trends or insights that emerge from the collective content
6. Unique characteristics or differentiators of this group
7. Common posting styles or engagement patterns on Reddit
8. Key value propositions or strengths evident across the group

Write in a natural, engaging way that provides insights into this collective group of Reddit users.

Respond with ONLY the summary text, no JSON or formatting.""",

    "reddit_traits_generation_prompt": """You are an expert at analyzing Reddit user profiles and generating structured trait data. Always return valid JSON only, no additional text.

Analyze the following group of {total_profiles} Reddit user profiles and generate traits in JSON format.

Individual Profile Summaries:
{combined_profile_summaries_text}

Based on these profiles, generate a traits JSON object with exactly 5 traits. Each trait must have:
- title: One of these exact titles (keep them as-is):
  1. "Skills & Expertise"
  2. "Working Style"
  3. "Motivations & Values"
  4. "Pain Points & Needs"
  5. "Organizational Leadership & Psychographic Profile"

- keywordTags: An array of 4-6 specific keyword tags relevant to this group of Reddit profiles
- descriptions: An array of 4-6 descriptive sentences (one per keywordTag) that explain how these tags apply to this specific group

Return ONLY valid JSON in this exact format:
{{
  "traits": [
    {{
      "title": "Skills & Expertise",
      "keywordTags": ["tag1", "tag2", "tag3", "tag4", "tag5"],
      "descriptions": ["description1", "description2", "description3", "description4", "description5"]
    }},
    ...
  ]
}}

Make sure the JSON is valid and properly formatted. Do not include any text before or after the JSON."""
}

prompt_service = PromptService(
    api_key=langsmith_api_key,
    cache_duration=600,
    fallback_prompts=FALLBACK_PROMPTS
)

reddit_group_summary_prompt = CachedPrompt("reddit_group_summary_prompt", prompt_service)
reddit_traits_generation_prompt = CachedPrompt("reddit_traits_generation_prompt", prompt_service)

__all__ = [
    "prompt_service",
    "reddit_group_summary_prompt",
    "reddit_traits_generation_prompt",
]
