"""OpenAI service for summary generation."""
import json
import logging
import asyncio
from typing import List, Dict, Any, Optional
from fastapi import HTTPException
from app.config import openai_client, logger
from prompts import (
    profile_posts_summary_prompt,
    profile_posts_batch_summary_prompt,
    combine_batch_summaries_prompt
)

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
    
    # Use LangSmith prompts - they fetch from LangSmith and cache for 10 minutes
    if is_final:
        # Use profile_posts_summary_prompt from LangSmith
        full_prompt = profile_posts_summary_prompt.format(
            profile_context=profile_context,
            total_posts=total_posts,
            text_for_analysis=text_for_analysis,
            profile_name=profile_name
        )
    else:
        # Use profile_posts_batch_summary_prompt from LangSmith
        full_prompt = profile_posts_batch_summary_prompt.format(
            profile_context=profile_context,
            total_posts=total_posts,
            text_for_analysis=text_for_analysis
        )
    
    # Split the prompt into system and user messages
    # LangSmith prompts are stored as: "System message\n\nUser message"
    if "\n\n" in full_prompt:
        parts = full_prompt.split("\n\n", 1)
        system_message = parts[0] if parts[0].startswith("You are") else "You are an expert at analyzing LinkedIn posts and generating professional summaries. Always respond with valid JSON only."
        user_prompt = parts[1] if len(parts) > 1 else full_prompt
    else:
        # Fallback if prompt doesn't have clear separation
        system_message = "You are an expert at analyzing LinkedIn posts and generating professional summaries. Always respond with valid JSON only."
        user_prompt = full_prompt
    
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
    
    # Use combine_batch_summaries_prompt from LangSmith
    full_combine_prompt = combine_batch_summaries_prompt.format(
        profile_context=profile_context,
        total_posts=len(post_texts),
        batch_count=len(batches),
        combined_summaries=combined_summaries,
        profile_name=profile_name
    )
    
    # Split the prompt into system and user messages
    if "\n\n" in full_combine_prompt:
        parts = full_combine_prompt.split("\n\n", 1)
        system_message = parts[0] if parts[0].startswith("You are") else "You are an expert at synthesizing multiple summaries into one comprehensive analysis. Always respond with valid JSON only."
        combine_prompt = parts[1] if len(parts) > 1 else full_combine_prompt
    else:
        # Fallback if prompt doesn't have clear separation
        system_message = "You are an expert at synthesizing multiple summaries into one comprehensive analysis. Always respond with valid JSON only."
        combine_prompt = full_combine_prompt
    
    try:
        result = await call_openai_with_retry(
            profile_id=profile_id,
            messages=[
                {"role": "system", "content": system_message},
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

