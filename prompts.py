import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Add the root directory to the path to allow importing PromptService
root_dir = Path(__file__).parent
sys.path.insert(0, str(root_dir))

from PromptService import PromptService, CachedPrompt
CACHE_DURATION = 600

# Initialize prompt service with optional API key
langsmith_api_key = os.getenv("LANGSMITH_API_KEY")
if not langsmith_api_key:
    import logging
    logging.warning("LANGSMITH_API_KEY not set. Prompt service may not work correctly.")
    # Use a dummy key to allow initialization, but it will fail when used
    langsmith_api_key = "dummy-key"

prompt_service = PromptService(api_key=langsmith_api_key, cache_duration=CACHE_DURATION)

analyze_query_prompt = CachedPrompt("analyze_query_prompt", prompt_service)
grade_results_prompt = CachedPrompt("grade_results_prompt", prompt_service)
generate_response_prompt = CachedPrompt("generate_response_prompt", prompt_service)
enrich_response_prompt = CachedPrompt("enrich_response_prompt", prompt_service)
grade_retrieved_data_prompt = CachedPrompt("grade_retrieved_data_prompt", prompt_service)
web_search_prompt = CachedPrompt("web_search_prompt", prompt_service)
web_search_synthesis_prompt = CachedPrompt("web_search_synthesis_prompt", prompt_service)
combined_generate_and_enrich_prompt = CachedPrompt("combined_generate_and_enrich_prompt", prompt_service)
pm_response_generation_prompt = CachedPrompt("pm_response_generation_prompt", prompt_service)
product_story_response_prompt = CachedPrompt("product_story_response_prompt", prompt_service)
page_context_response_prompt = CachedPrompt("page_context_response_prompt", prompt_service)
nba_response_generation_prompt = CachedPrompt("nba_response_generation_prompt", prompt_service)
general_chat_prompt = CachedPrompt("general_chat_prompt", prompt_service)
profile_search_response_prompt = CachedPrompt("profile_search_response_prompt", prompt_service)
user_journey_prompt = CachedPrompt("user_journey_prompt", prompt_service)
search_query_generation_prompt = CachedPrompt("search_query_generation_prompt", prompt_service)
dialpad_search_query_prompt = CachedPrompt("dialpad_search_query_prompt", prompt_service)
salesforce_search_query_prompt = CachedPrompt("salesforce_search_query_prompt", prompt_service)
slack_search_query_prompt = CachedPrompt("slack_search_query_prompt", prompt_service)
coda_search_query_prompt = CachedPrompt("coda_search_query_prompt", prompt_service)
shortcut_search_query_prompt = CachedPrompt("shortcut_search_query_prompt", prompt_service)
gong_search_query_prompt = CachedPrompt("gong_search_query_prompt", prompt_service)
socials_search_query_prompt = CachedPrompt("socials_search_query_prompt", prompt_service)
manual_upload_search_query_prompt = CachedPrompt("manual_upload_search_query_prompt", prompt_service)
website_search_query_prompt = CachedPrompt("website_search_query_prompt", prompt_service)
youtube_search_query_prompt = CachedPrompt("youtube_search_query_prompt", prompt_service)
product_story_search_query_prompt = CachedPrompt("product_story_search_query_prompt", prompt_service)
google_drive_search_query_prompt = CachedPrompt("google_drive_search_query_prompt", prompt_service)
web_search_query_prompt = CachedPrompt("web_search_query_prompt", prompt_service)
unknown_sources_search_query_prompt = CachedPrompt("unknown_sources_search_query_prompt", prompt_service)

# Summary-related prompts
profile_posts_summary_prompt = CachedPrompt("profile_posts_summary_prompt", prompt_service)
profile_posts_batch_summary_prompt = CachedPrompt("profile_posts_batch_summary_prompt", prompt_service)
combine_batch_summaries_prompt = CachedPrompt("combine_batch_summaries_prompt", prompt_service)
profile_comments_summary_prompt = CachedPrompt("profile_comments_summary_prompt", prompt_service)
group_summary_prompt = CachedPrompt("group_summary_prompt", prompt_service)
traits_generation_prompt = CachedPrompt("traits_generation_prompt", prompt_service)

__all__ = [
    "analyze_query_prompt",
    "grade_results_prompt",
    "generate_response_prompt",
    "enrich_response_prompt",
    "grade_retrieved_data_prompt",
    "web_search_prompt", 
    "web_search_synthesis_prompt",
    "combined_generate_and_enrich_prompt",
    "pm_response_generation_prompt",
    "product_story_response_prompt",
    "page_context_response_prompt",
    "nba_response_generation_prompt",
    "general_chat_prompt",
    "profile_search_response_prompt",
    "user_journey_prompt",
    "nba_response_generation_prompt",
    "search_query_generation_prompt",
    "dialpad_search_query_prompt",
    "salesforce_search_query_prompt",
    "slack_search_query_prompt",
    "coda_search_query_prompt",
    "shortcut_search_query_prompt",
    "gong_search_query_prompt",
    "socials_search_query_prompt",
    "manual_upload_search_query_prompt",
    "website_search_query_prompt",
    "youtube_search_query_prompt",
    "product_story_search_query_prompt",
    "google_drive_search_query_prompt",
    "web_search_query_prompt",
    "unknown_sources_search_query_prompt",
    # Summary-related prompts
    "profile_posts_summary_prompt",
    "profile_posts_batch_summary_prompt",
    "combine_batch_summaries_prompt",
    "profile_comments_summary_prompt",
    "group_summary_prompt",
    "traits_generation_prompt"
]