"""OpenAI service for summary generation."""
import json
import logging
import asyncio
from typing import List, Dict, Any, Optional
from fastapi import HTTPException
from app.config import openai_client, logger

# Re-export logger for convenience
__all__ = [
    "call_openai_with_retry",
    "generate_summary_for_batch",
    "generate_profile_summary_from_posts",
]


async def call_openai_with_retry(
    profile_id: str,
    messages: List[Dict[str, str]],
    max_tokens: int,
    max_retries: int = 3,
    initial_delay: float = 1.0,
    validate_summary: bool = False
) -> Dict[str, Any]:
    """
    Call OpenAI API with exponential backoff retry logic.
    
    Args:
        profile_id: Profile ID for logging
        messages: List of message dicts for OpenAI API
        max_tokens: Maximum tokens for the completion
        max_retries: Maximum number of retry attempts
        initial_delay: Initial delay in seconds before first retry
        validate_summary: If True, validates that summary is not empty (for final summaries only)
    
    Returns:
        Parsed JSON result from OpenAI
    
    Raises:
        Exception: If all retries fail
    """
    last_exception = None
    
    for attempt in range(max_retries):
        try:
            completion = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                max_tokens=max_tokens,
                temperature=0.3,
                response_format={"type": "json_object"}
            )
            
            result = json.loads(completion.choices[0].message.content)
            
            # Validate that we got a summary (only for final summaries)
            if validate_summary and (not result.get("summary") or not result.get("summary", "").strip()):
                raise ValueError("OpenAI returned empty summary")
            
            return result
            
        except json.JSONDecodeError as e:
            last_exception = e
            logger.warning(f"Profile {profile_id}: JSON decode error on attempt {attempt + 1}/{max_retries}: {e}")
            if attempt < max_retries - 1:
                delay = initial_delay * (2 ** attempt)
                logger.info(f"Profile {profile_id}: Retrying in {delay:.1f} seconds...")
                await asyncio.sleep(delay)
            else:
                logger.error(f"Profile {profile_id}: Failed to parse JSON after {max_retries} attempts")
                raise
                
        except Exception as e:
            last_exception = e
            error_str = str(e).lower()
            
            # Check if it's a rate limit error
            is_rate_limit = (
                "rate limit" in error_str or 
                "429" in error_str or
                "too many requests" in error_str
            )
            
            if is_rate_limit:
                # Longer delay for rate limits
                delay = initial_delay * (2 ** attempt) * 2  # Double the delay for rate limits
                logger.warning(f"Profile {profile_id}: Rate limit hit on attempt {attempt + 1}/{max_retries}. Waiting {delay:.1f} seconds...")
            else:
                delay = initial_delay * (2 ** attempt)
                logger.warning(f"Profile {profile_id}: Error on attempt {attempt + 1}/{max_retries}: {e}")
            
            if attempt < max_retries - 1:
                await asyncio.sleep(delay)
            else:
                logger.error(f"Profile {profile_id}: Failed after {max_retries} attempts: {e}")
                raise
    
    # Should never reach here, but just in case
    if last_exception:
        raise last_exception
    raise Exception("Unknown error in retry logic")


async def generate_summary_for_batch(
    profile_id: str,
    profile_name: str,
    profile_context: str,
    post_texts: List[str],
    total_posts: int,
    is_final: bool = True
) -> Dict[str, Any]:
    """
    Generate summary for a single batch of posts.
    Helper function for batched summary generation.
    """
    text_for_analysis = "\n\n".join(post_texts)
    
    if is_final:
        instruction = f"""Generate a comprehensive, detailed analysis:
1. A thorough 5-8 sentence summary that covers:
   - Their current role and company context (mention company stage if evident: Series A/B, startup, growth stage, etc.)
   - Main topics, themes, and subjects they frequently post about
   - Their posting style and tone (technical, thought leadership, personal reflections, etc.)
   - Key insights, opinions, expertise areas, or perspectives they share
   - Notable patterns in content (technical depth, problem-solving focus, industry commentary, etc.)
   - Engagement patterns or community involvement if evident
   - Any unique value propositions or differentiators in their content
   
   Start with "{profile_name} is currently..." or "{profile_name} has..." and write in a natural, engaging way.
   
2. Extract 4-6 key highlights/badges (similar to: "Early + Growth", "Fullstack", "Thought Leader", "Technical Expert", "Series B", "Problem Solver", "Startup Experience", etc.)

3. Identify 10-15 important keywords/phrases for highlighting"""
    else:
        instruction = f"""Analyze this batch of posts and provide:
1. A 3-4 sentence summary of the key themes, topics, and insights from these posts
2. Extract 3-4 key highlights/badges that apply to these posts
3. Identify 8-10 important keywords/phrases mentioned

This is a partial analysis that will be combined with other batches."""
    
    user_prompt = f"""Analyze the LinkedIn posts from {profile_context}.

Posts ({total_posts} in this batch):
{text_for_analysis}

{instruction}

Respond in JSON format only:
{{
    "summary": "Summary text",
    "highlights": ["Highlight 1", "Highlight 2", ...],
    "keywords": ["keyword1", "keyword2", ...]
}}"""
    
    system_message = "You are an expert at analyzing LinkedIn posts and generating professional summaries. Always respond with valid JSON only."
    
    try:
        result = await call_openai_with_retry(
            profile_id=profile_id,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_prompt}
            ],
            max_tokens=1000 if not is_final else 1500,
            max_retries=3,
            initial_delay=1.0,
            validate_summary=is_final  # Only validate summary for final batches
        )
        
        return {
            "summary": result.get("summary", ""),
            "highlights": result.get("highlights", []),
            "keywords": result.get("keywords", [])
        }
    except Exception as e:
        logger.error(f"Error generating batch summary for profile {profile_id} after retries: {e}")
        logger.error(f"Exception type: {type(e).__name__}")
        return {
            "summary": None,
            "highlights": [],
            "keywords": []
        }


async def generate_profile_summary_from_posts(
    profile_id: str,
    profile_name: str,
    profile_title: Optional[str],
    profile_company: Optional[str],
    posts: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Generate summary, keywords, and highlights for a profile based on their posts.
    Uses map-reduce batching to handle large numbers of posts without hitting context limits.
    
    Args:
        profile_id: Profile ID
        profile_name: Profile name
        profile_title: Profile title/role (optional)
        profile_company: Profile company (optional)
        posts: List of post objects
    
    Returns:
        Dictionary with 'summary', 'highlights', and 'keywords' keys
    """
    if not openai_client:
        raise HTTPException(status_code=503, detail="OpenAI client not initialized. Please check OPENAI_API_KEY.")
    
    if not posts:
        return {
            "summary": None,
            "highlights": [],
            "keywords": []
        }
    
    # Extract post text content
    post_texts = []
    for post in posts:
        text = post.get("text", "")
        if text and text.strip():
            post_texts.append(text.strip())
    
    if not post_texts:
        return {
            "summary": None,
            "highlights": [],
            "keywords": []
        }
    
    # Build profile context
    if profile_title and profile_company:
        profile_context = f"{profile_name}, who is a {profile_title} at {profile_company}"
    elif profile_company:
        profile_context = f"{profile_name}, who works at {profile_company}"
    else:
        profile_context = profile_name
    
    # Batch configuration to avoid context limits
    POSTS_PER_BATCH = 100
    MAX_CHARS_PER_BATCH = 200000  # ~50k tokens, optimized for Tier 2
    
    # Split posts into batches
    batches = []
    current_batch = []
    current_chars = 0
    
    for text in post_texts:
        # Start new batch if adding this post would exceed limits
        if len(current_batch) >= POSTS_PER_BATCH or (current_chars + len(text) > MAX_CHARS_PER_BATCH and current_batch):
            batches.append(current_batch)
            current_batch = []
            current_chars = 0
        
        current_batch.append(text)
        current_chars += len(text)
    
    # Don't forget the last batch
    if current_batch:
        batches.append(current_batch)
    
    logger.info(f"Profile {profile_id}: Processing {len(post_texts)} posts in {len(batches)} batch(es)")
    
    # If only one batch, process directly (no need for map-reduce)
    if len(batches) == 1:
        return await generate_summary_for_batch(
            profile_id, profile_name, profile_context, batches[0], len(post_texts), is_final=True
        )
    
    # Map phase: Generate intermediate summaries for each batch
    batch_summaries = []
    all_keywords = []
    all_highlights = []
    
    for idx, batch in enumerate(batches):
        logger.info(f"Profile {profile_id}: Processing batch {idx + 1}/{len(batches)} ({len(batch)} posts)")
        
        batch_result = await generate_summary_for_batch(
            profile_id, profile_name, profile_context, batch, len(batch), is_final=False
        )
        
        if batch_result.get("summary"):
            batch_summaries.append(batch_result["summary"])
        if batch_result.get("keywords"):
            all_keywords.extend(batch_result["keywords"])
        if batch_result.get("highlights"):
            all_highlights.extend(batch_result["highlights"])
        
        # Increased delay between batches to avoid rate limits
        await asyncio.sleep(0.8)  # Increased from 0.3 to 0.8 seconds
    
    # Reduce phase: Combine batch summaries into final summary
    if not batch_summaries:
        logger.error(f"Profile {profile_id}: All batches failed - no summaries to combine")
        return {
            "summary": None,
            "highlights": [],
            "keywords": []
        }
    
    # Deduplicate keywords and highlights
    unique_keywords = list(dict.fromkeys(all_keywords))[:15]  # Keep top 15 unique
    unique_highlights = list(dict.fromkeys(all_highlights))[:6]  # Keep top 6 unique
    
    # Generate final combined summary from batch summaries
    combined_summaries = "\n\n".join([f"Batch {i+1}: {s}" for i, s in enumerate(batch_summaries)])
    
    combine_prompt = f"""You are combining multiple analysis summaries of LinkedIn posts from {profile_context} into ONE final comprehensive summary.

Here are the summaries from analyzing {len(post_texts)} total posts in {len(batches)} batches:

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
}}"""
    
    try:
        result = await call_openai_with_retry(
            profile_id=profile_id,
            messages=[
                {"role": "system", "content": "You are an expert at synthesizing multiple summaries into one comprehensive analysis. Always respond with valid JSON only."},
                {"role": "user", "content": combine_prompt}
            ],
            max_tokens=1500,
            max_retries=3,
            initial_delay=1.0,
            validate_summary=True  # Always validate for combine phase (final summary)
        )
        
        return {
            "summary": result.get("summary", ""),
            "highlights": result.get("highlights", unique_highlights),
            "keywords": result.get("keywords", unique_keywords)
        }
    except Exception as e:
        logger.error(f"Error combining batch summaries for profile {profile_id} after retries: {e}")
        logger.error(f"Exception type: {type(e).__name__}")
        # Fallback: return first batch summary with collected keywords/highlights
        fallback_summary = batch_summaries[0] if batch_summaries else None
        if not fallback_summary:
            logger.error(f"Profile {profile_id}: No batch summaries available for fallback")
        return {
            "summary": fallback_summary,
            "highlights": unique_highlights,
            "keywords": unique_keywords
        }

