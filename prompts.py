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

# Define fallback prompts for summary-related prompts (used when LangSmith is unavailable)
FALLBACK_PROMPTS = {
    "profile_posts_summary_prompt": """You are an expert at analyzing LinkedIn posts and generating comprehensive, detailed professional summaries. Write thorough, informative summaries that capture the essence and depth of the person's posting style and content. Always respond with valid JSON only.

Analyze the LinkedIn posts from {profile_context}.

Posts ({total_posts} in this batch):
{text_for_analysis}

Generate a comprehensive, detailed analysis:
1. A thorough 5-8 sentence summary that covers:
   - Their current role and company context (mention company stage if evident: Series A/B, startup, growth stage, etc.)
   - Main topics, themes, and subjects they frequently post about
   - Their posting style and tone (technical, thought leadership, personal reflections, etc.)
   - Key insights, opinions, expertise areas, or perspectives they share
   - Notable patterns in content (technical depth, problem-solving focus, industry commentary, etc.)
   - Engagement patterns or community involvement if evident
   - Any unique value propositions or differentiators in their content
   
   Start with "{profile_name} is currently..." or "{profile_name} has..." and write in a natural, engaging way.
   
2. Extract 4-6 key highlights/badges (similar to: "Early + Growth", "Fullstack", "Thought Leader", "Technical Expert", "Series B", "Problem Solver", "Startup Experience", etc.) based on:
   - Company stage mentioned (Series A, B, growth stage, etc.)
   - Technical skills and expertise demonstrated
   - Content themes and posting style (thought leadership, technical depth, etc.)
   - Career patterns or notable experiences mentioned
   - Industry recognition or patterns in their posts
   
3. Identify 10-15 important keywords/phrases that should be highlighted (for keyword highlighting):
   - Technical skills, tools, frameworks, or technologies mentioned
   - Programming languages, platforms, or methodologies
   - Key themes, topics, or subject areas
   - Company names, industries, or domains
   - Concepts, practices, or philosophies discussed

Respond in JSON format only:
{{
    "summary": "Detailed 5-8 sentence comprehensive summary starting with the person's name",
    "highlights": ["Highlight 1", "Highlight 2", ...],
    "keywords": ["keyword1", "keyword2", ...]
}}""",
    
    "profile_posts_batch_summary_prompt": """You are an expert at analyzing LinkedIn posts and generating professional summaries. Always respond with valid JSON only.

Analyze the LinkedIn posts from {profile_context}.

Posts ({total_posts} in this batch):
{text_for_analysis}

Analyze this batch of posts and provide:
1. A 3-4 sentence summary of the key themes, topics, and insights from these posts
2. Extract 3-4 key highlights/badges that apply to these posts
3. Identify 8-10 important keywords/phrases mentioned

This is a partial analysis that will be combined with other batches.

Respond in JSON format only:
{{
    "summary": "Summary text",
    "highlights": ["Highlight 1", "Highlight 2", ...],
    "keywords": ["keyword1", "keyword2", ...]
}}""",
    
    "combine_batch_summaries_prompt": """You are an expert at synthesizing multiple summaries into one comprehensive analysis. Always respond with valid JSON only.

You are combining multiple analysis summaries of LinkedIn posts from {profile_context} into ONE final comprehensive summary.

Here are the summaries from analyzing {total_posts} total posts in {batch_count} batches:

{combined_summaries}

Based on ALL these batch summaries, create ONE unified final analysis:

1. A comprehensive 5-8 sentence summary that synthesizes all the insights, covering:
   - Their current role and company context
   - Main topics and themes across ALL their posts
   - Their posting style and tone
   - Key insights, expertise areas, and perspectives
   - Notable patterns in their content
   
   Start with "{profile_name} is currently..." or "{profile_name} has..."

2. Select the 4-6 BEST highlights/badges from across all batches that best represent this person

3. Select the 10-15 MOST important keywords from across all batches

Respond in JSON format only:
{{
    "summary": "Final comprehensive 5-8 sentence summary",
    "highlights": ["Best Highlight 1", "Best Highlight 2", ...],
    "keywords": ["keyword1", "keyword2", ...]
}}""",
    
    "profile_comments_summary_prompt": """You are an expert at analyzing LinkedIn comments and engagement patterns. Write thorough, informative summaries that capture how people engage with content through comments. Always respond with valid JSON only.

Analyze the LinkedIn comments made by {profile_name}, who is a {profile_title} at {profile_company}.

This person has commented on {total_comments} posts. For each post, both the original post content and {profile_name}'s comment are provided.

Posts and Comments ({total_comments} total):
{text_for_analysis}

Generate a comprehensive, detailed analysis:
1. A thorough 5-8 sentence summary that covers:
   - The types of posts they engage with (topics, themes, industries)
   - Their commenting style and tone (technical, supportive, thought-provoking, etc.)
   - Key insights, opinions, or expertise they share through comments
   - Patterns in what content they choose to engage with
   - The value they add through their comments (questions, insights, experiences, etc.)
   - Their role as a community member or thought leader through comments
   - Notable themes or subjects they frequently comment on
   
   Start with "{profile_name} actively engages..." or "{profile_name} frequently comments..." and write in a natural, engaging way.
   
2. Extract 4-6 key highlights/badges based on:
   - Types of content they engage with (technical, leadership, industry news, etc.)
   - Commenting style (thoughtful, supportive, technical depth, etc.)
   - Expertise areas demonstrated through comments
   - Community engagement patterns
   
3. Identify 10-15 important keywords/phrases:
   - Technical skills, tools, or technologies mentioned in comments
   - Topics or themes they frequently comment on
   - Industries or domains they engage with
   - Concepts or methodologies discussed

Respond in JSON format only:
{{
    "summary": "Detailed 5-8 sentence comprehensive summary starting with the person's name",
    "highlights": ["Highlight 1", "Highlight 2", ...],
    "keywords": ["keyword1", "keyword2", ...]
}}""",
    
    "group_summary_prompt": """You are an expert at analyzing groups of LinkedIn profiles and generating comprehensive, insightful high-level summaries. Write detailed, informative summaries that capture collective patterns and insights.

Analyze the following group of {total_profiles} profiles who work at {company_type}.

Companies represented: {company_list}

Individual Profile Summaries:
{combined_summaries}

Generate a comprehensive high-level summary (6-10 sentences) that covers:
1. Overall themes and patterns across all profiles in this group
2. Common topics, technologies, or expertise areas shared among them
3. Company culture and stage characteristics evident from their posts
4. Professional focus areas (e.g., technical depth, thought leadership, product development)
5. Industry trends or insights that emerge from the collective content
6. Unique characteristics or differentiators of this group
7. Common posting styles or engagement patterns
8. Key value propositions or strengths evident across the group

Write in a natural, engaging way that provides insights into this collective group of professionals from {company_type}.

Respond with ONLY the summary text, no JSON or formatting.""",
    
    "traits_generation_prompt": """You are an expert at analyzing professional profiles and generating structured trait data. Always return valid JSON only, no additional text.

Analyze the following group of {total_profiles} profiles and generate traits in JSON format.

Individual Profile Summaries:
{combined_summaries}

Based on these profiles, generate a traits JSON object with exactly 5 traits. Each trait must have:
- title: One of these exact titles (keep them as-is):
  1. "Skills & Expertise"
  2. "Working Style"
  3. "Motivations & Values"
  4. "Pain Points & Needs"
  5. "Organizational Leadership & Psychographic Profile"

- keywordTags: An array of 4-6 specific keyword tags relevant to this group of profiles
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

# Initialize prompt service with optional API key
langsmith_api_key = os.getenv("LANGSMITH_API_KEY")
if not langsmith_api_key:
    import logging
    logging.warning("LANGSMITH_API_KEY not set. Prompt service may not work correctly.")
    # Use a dummy key to allow initialization, but it will fail when used
    langsmith_api_key = "dummy-key"

prompt_service = PromptService(api_key=langsmith_api_key, cache_duration=CACHE_DURATION, fallback_prompts=FALLBACK_PROMPTS)

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