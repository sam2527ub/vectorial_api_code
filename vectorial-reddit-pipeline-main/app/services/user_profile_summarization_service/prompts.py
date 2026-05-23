"""User profile summarization prompts loaded from LangSmith."""
import os
from PromptService import PromptService, CachedPrompt

langsmith_api_key = os.getenv("LANGSMITH_API_KEY")

FALLBACK_PROMPTS = {
    "reddit_profile_posts_summary_prompt": """You are an expert at analyzing Reddit posts and comments and generating professional summaries. Always respond with valid JSON only.

Analyze the Reddit posts and comments from {profile_context}.

Activities ({total_activities} in this batch - includes both posts and comments):
{text_for_analysis}

Generate a comprehensive, detailed analysis:
1. A thorough 5-8 sentence summary that covers:
   - Their main topics, themes, and subjects they frequently discuss in Reddit posts and comments
   - Their posting and commenting style and tone (technical, helpful, opinionated, etc.)
   - Key insights, opinions, expertise areas, or perspectives they share
   - Notable patterns in content (technical depth, problem-solving focus, community engagement, etc.)
   - Engagement patterns or community involvement if evident
   - Any unique value propositions or differentiators in their content
   
   Start with "{profile_name} is..." or "{profile_name} has..." and write in a natural, engaging way.
   
2. Extract 4-6 key highlights/badges (similar to: "Active Community Member", "Technical Expert", "Problem Solver", "Helpful Contributor", "Thought Leader", etc.)

3. Identify 10-15 important keywords/phrases for highlighting

Respond in JSON format only:
{{
    "summary": "Summary text",
    "highlights": ["Highlight 1", "Highlight 2", ...],
    "keywords": ["keyword1", "keyword2", ...]
}}""",

    "reddit_profile_posts_batch_summary_prompt": """You are an expert at analyzing Reddit posts and comments and generating professional summaries. Always respond with valid JSON only.

Analyze the Reddit posts and comments from {profile_context}.

Activities ({total_activities} in this batch - includes both posts and comments):
{text_for_analysis}

Analyze this batch of Reddit activities (posts and comments) and provide:
1. A 3-4 sentence summary of the key themes, topics, and insights from these activities
2. Extract 3-4 key highlights/badges that apply to these activities
3. Identify 8-10 important keywords/phrases mentioned

This is a partial analysis that will be combined with other batches.

Respond in JSON format only:
{{
    "summary": "Summary text",
    "highlights": ["Highlight 1", "Highlight 2", ...],
    "keywords": ["keyword1", "keyword2", ...]
}}""",

    "reddit_combine_batch_summaries_prompt": """You are an expert at synthesizing multiple summaries into one comprehensive analysis. Always respond with valid JSON only.

You are combining multiple analysis summaries of Reddit posts and comments from {profile_context} into ONE final comprehensive summary.

Here are the summaries from analyzing {total_activities} total activities (posts and comments) in {batch_count} batches:

{combined_batch_summaries_text}

Based on ALL these batch summaries, create ONE unified final analysis:

1. A comprehensive 5-8 sentence summary that synthesizes all the insights, covering:
   - Main topics and themes across ALL their posts and comments
   - Their posting and commenting style and tone
   - Key insights, expertise areas, and perspectives they share
   - Notable patterns in their content
   - Engagement patterns or community involvement
   
   Start with "{profile_name} is..." or "{profile_name} has..."

2. Select the 4-6 BEST highlights/badges from across all batches that best represent this person

3. Select the 10-15 MOST important keywords from across all batches

Respond in JSON format only:
{{
    "summary": "Final comprehensive 5-8 sentence summary",
    "highlights": ["Best Highlight 1", "Best Highlight 2", ...],
    "keywords": ["keyword1", "keyword2", ...]
}}"""
}

prompt_service = PromptService(
    api_key=langsmith_api_key,
    cache_duration=600,
    fallback_prompts=FALLBACK_PROMPTS
)

reddit_profile_posts_summary_prompt = CachedPrompt("reddit_profile_posts_summary_prompt", prompt_service)
reddit_profile_posts_batch_summary_prompt = CachedPrompt("reddit_profile_posts_batch_summary_prompt", prompt_service)
reddit_combine_batch_summaries_prompt = CachedPrompt("reddit_combine_batch_summaries_prompt", prompt_service)

__all__ = [
    "prompt_service",
    "reddit_profile_posts_summary_prompt",
    "reddit_profile_posts_batch_summary_prompt",
    "reddit_combine_batch_summaries_prompt",
]
