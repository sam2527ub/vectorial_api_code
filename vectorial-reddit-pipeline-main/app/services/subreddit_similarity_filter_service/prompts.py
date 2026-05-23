"""Subreddit similarity filter prompts loaded from LangSmith."""
import os
from PromptService import PromptService, CachedPrompt

langsmith_api_key = os.getenv("LANGSMITH_API_KEY")

FALLBACK_PROMPTS = {
    "reddit_subreddit_semantic_similarity_prompt": """You are a helpful assistant that determines semantic similarity between subreddit names. Always respond with valid JSON only.

You are a lenient semantic similarity checker for Reddit subreddit names. Prefer inclusion: when unsure, answer true. Use false only when a user subreddit is clearly unrelated to ALL matched targets (different hobby, unrelated industry, pure meme/joke subs with no topical overlap, etc.).

Given matched subreddits (campaign targets) and user subreddits (from a user's activity), decide whether each user subreddit is similar or plausibly related to ANY matched subreddit.

Matched subreddits (targets):
{matched_subreddits}

User subreddits to check:
{user_subreddits}

For each user subreddit, answer true if it is similar, related, adjacent, or tangentially on-topic to ANY matched subreddit. Answer false only if it is clearly irrelevant (no reasonable thematic, professional, or hobby overlap). Guidelines:
- Prefer true: broad topics, same ecosystem, adjacent niches, abbreviations, spelling variants, regional or naming differences
- Prefer true: same industry vertical, tooling stack, or audience even if wording differs
- Use false sparingly: only for obviously off-topic subs (e.g. unrelated entertainment, unrelated geography, no shared theme with any target)

Return ONLY a valid JSON object mapping each user subreddit name (lowercase) to a boolean (true = keep / related, false = clearly irrelevant).

Example format:
{{
  "supplychain": true,
  "logistics": true,
  "random": false,
  "unrelated": false
}}

Return ONLY the JSON object, no other text:"""
}

prompt_service = PromptService(
    api_key=langsmith_api_key,
    cache_duration=600,
    fallback_prompts=FALLBACK_PROMPTS
)

reddit_subreddit_semantic_similarity_prompt = CachedPrompt("reddit_subreddit_semantic_similarity_prompt", prompt_service)

__all__ = [
    "prompt_service",
    "reddit_subreddit_semantic_similarity_prompt",
]
