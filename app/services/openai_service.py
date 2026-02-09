"""OpenAI service for summary generation."""
import json
import logging
import asyncio
from typing import List, Dict, Any, Optional, Tuple
from fastapi import HTTPException
from app.config import openai_client, anthropic_client, logger
from app.services.ai_gateway_service import ai_gateway
from app.services.dynamic_context_window_management_service import context_manager
from app.services.direct_api_handler import DirectAPIHandler
from prompts import (
    profile_posts_summary_prompt,
    profile_posts_batch_summary_prompt,
    combine_batch_summaries_prompt
)

# Initialize direct API handler for fallback
_direct_handler = DirectAPIHandler()

# Re-export logger for convenience
__all__ = [
    "call_openai_with_retry",
    "call_claude_with_retry",
    "generate_summary_for_batch",
    "generate_profile_summary_from_posts",
]


def split_prompt_into_messages(
    full_prompt: str,
    default_system_message: str = "You are an expert at analyzing LinkedIn posts and generating professional summaries. Always respond with valid JSON only."
) -> Tuple[str, str]:
    """
    Split a LangSmith prompt into system and user messages.
    
    LangSmith prompts are stored as: "System message\n\nUser message"
    
    Args:
        full_prompt: The full prompt text
        default_system_message: Default system message if prompt doesn't have clear separation
    
    Returns:
        Tuple of (system_message, user_message)
    """
    if "\n\n" in full_prompt:
        parts = full_prompt.split("\n\n", 1)
        system_message = parts[0] if parts[0].startswith("You are") else default_system_message
        user_message = parts[1] if len(parts) > 1 else full_prompt
    else:
        # Fallback if prompt doesn't have clear separation
        system_message = default_system_message
        user_message = full_prompt
    
    return system_message, user_message


async def call_openai_with_retry(
    profile_id: str,
    messages: List[Dict[str, str]],
    max_tokens: int,
    max_retries: int = 3,
    initial_delay: float = 1.0,
    validate_summary: bool = False,
    model: Optional[str] = None
) -> Dict[str, Any]:
    """
    Call OpenAI API with exponential backoff retry logic.
    Uses AI Gateway if enabled, otherwise falls back to direct API.
    
    Args:
        profile_id: Profile ID for logging
        messages: List of message dicts for OpenAI API
        max_tokens: Maximum tokens for the completion
        max_retries: Maximum number of retry attempts
        initial_delay: Initial delay in seconds before first retry
        validate_summary: If True, validates that summary is not empty (for final summaries only)
        model: Optional model name (defaults to profile_summary_default from config)
    
    Returns:
        Parsed JSON result from OpenAI
    
    Raises:
        Exception: If all retries fail
    """
    # Use AI Gateway if enabled
    if ai_gateway.enabled:
        try:
            effective_model = model or "openai/gpt-5-mini"
            return await ai_gateway.call_via_gateway(
                context_id=profile_id,
                messages=messages,
                max_tokens=max_tokens,
                model=effective_model,
                default_model="openai/gpt-5-mini",
                fallback_models=["anthropic/claude-sonnet-4.5", "openai/gpt-4o-mini"],
                config_default_attr='profile_summary_default',
                config_fallbacks_attr='profile_summary_fallbacks',
                hardcoded_default="openai/gpt-5-mini",
                validate_summary=validate_summary,
                return_text=False,
                direct_api_fallback_model=effective_model
            )
        except Exception as e:
            logger.warning(f"Profile {profile_id}: Gateway call failed, falling back to direct API: {e}")
            # Fall through to direct API
    
    # Direct API fallback (original logic with retries)
    last_exception = None
    
    for attempt in range(max_retries):
        try:
            result = await _direct_handler.call_openai_direct(
                profile_id=profile_id,
                messages=messages,
                max_tokens=max_tokens,
                validate_summary=validate_summary
            )
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


async def call_claude_with_retry(
    context_id: str,
    messages: List[Dict[str, str]],
    max_tokens: int,
    max_retries: int = 3,
    initial_delay: float = 1.0,
    model: str = "claude-sonnet-4-5-20250929"  # Specific snapshot for production stability (format: claude-sonnet-4-5-YYYYMMDD)
) -> str:
    """
    Call Anthropic Claude API with exponential backoff retry logic.
    Uses AI Gateway if enabled, otherwise falls back to direct API.
    
    Args:
        context_id: Context ID for logging (e.g., audience_room_id)
        messages: List of message dicts for Claude API (must have 'role' and 'content')
        max_tokens: Maximum tokens for the completion
        max_retries: Maximum number of retry attempts
        initial_delay: Initial delay in seconds before first retry
        model: Claude model to use (default: claude-sonnet-4-5-20250929 - specific snapshot for production)
    
    Returns:
        Response text from Claude
    
    Raises:
        Exception: If all retries fail
    """
    # Use AI Gateway if enabled
    if ai_gateway.enabled:
        try:
            # Handle legacy model format
            effective_model = None
            if model and model != "claude-sonnet-4-5-20250929":
                effective_model = model
            elif model == "claude-sonnet-4-5-20250929":
                effective_model = None  # Use default from config
            
            def handle_legacy_model(m: Optional[str], default: str) -> Optional[str]:
                """Handle legacy Claude model name format."""
                if m == "claude-sonnet-4-5-20250929":
                    return None
                return m
            
            result = await ai_gateway.call_via_gateway(
                context_id=context_id,
                messages=messages,
                max_tokens=max_tokens,
                model=effective_model,
                default_model="anthropic/claude-sonnet-4.5",
                fallback_models=["openai/gpt-5-mini", "openai/gpt-4o-mini"],
                config_default_attr='group_summary_default',
                config_fallbacks_attr='group_summary_fallbacks',
                hardcoded_default="anthropic/claude-sonnet-4.5",
                validate_summary=False,
                return_text=True,
                legacy_model_handler=handle_legacy_model,
                direct_api_fallback_model=model
            )
            
            return result if isinstance(result, str) else str(result)
        except Exception as e:
            logger.warning(f"Context {context_id}: Gateway call failed, falling back to direct API: {e}")
            # Fall through to direct API
    
    # Direct API fallback (original logic with retries)
    if not anthropic_client:
        raise HTTPException(status_code=503, detail="Anthropic client not initialized. Please check ANTHROPIC_API_KEY.")
    
    last_exception = None
    
    # Extract system message if present (Claude API requires separate system parameter)
    system_message = None
    user_messages = []
    
    for msg in messages:
        if msg.get("role") == "system":
            system_message = msg.get("content", "")
        else:
            user_messages.append(msg)
    
    # Ensure we have at least one user message
    if not user_messages:
        raise ValueError("At least one non-system message is required for Claude API")
    
    for attempt in range(max_retries):
        try:
            result = await _direct_handler.call_claude_direct(
                context_id=context_id,
                messages=messages,
                max_tokens=max_tokens,
                model=model
            )
            return result
            
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
                logger.warning(f"Context {context_id}: Rate limit hit on attempt {attempt + 1}/{max_retries}. Waiting {delay:.1f} seconds...")
            else:
                delay = initial_delay * (2 ** attempt)
                logger.warning(f"Context {context_id}: Error on attempt {attempt + 1}/{max_retries}: {e}")
            
            if attempt < max_retries - 1:
                await asyncio.sleep(delay)
            else:
                logger.error(f"Context {context_id}: Failed after {max_retries} attempts: {e}")
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
    is_final: bool = True,
    model: Optional[str] = None
) -> Dict[str, Any]:
    """
    Generate summary for a single batch of posts.
    Helper function for batched summary generation.
    Uses dynamic context window management to ensure content fits.
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
    system_message, user_prompt = split_prompt_into_messages(full_prompt)
    
    # Use dynamic context window management
    max_completion_tokens = 1000 if not is_final else 1500
    effective_model = model or "openai/gpt-5-mini"
    
    adjusted_user_prompt, adjust_metadata = context_manager.adjust_content_to_fit_context_window(
        content=user_prompt,
        system_message=system_message,
        model_name=effective_model,
        max_completion_tokens=max_completion_tokens
    )
    
    if adjust_metadata.get("truncated"):
        logger.warning(
            f"Profile {profile_id}: Batch content truncated "
            f"({adjust_metadata.get('truncation_ratio', 0):.1%} reduction) "
            f"to fit {effective_model} context window"
        )
    
    try:
        result = await call_openai_with_retry(
            profile_id=profile_id,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": adjusted_user_prompt}
            ],
            max_tokens=max_completion_tokens,
            max_retries=3,
            initial_delay=1.0,
            validate_summary=is_final,  # Only validate summary for final batches
            model=effective_model
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
    
    # Use dynamic context window management for optimal batching
    model = "openai/gpt-5-mini"  # Default model for profile summaries
    max_completion_tokens = 1500
    
    # Prepare a temporary system message for batch calculation
    # This is used to estimate token usage
    temp_system_message = "You are an expert at analyzing LinkedIn posts and generating professional summaries. Always respond with valid JSON only."
    
    # Calculate optimal batches using dynamic context window management
    batches, batch_metadata = context_manager.calculate_optimal_batch_sizes_for_activities(
        activity_text_list=post_texts,
        system_message=temp_system_message,
        model_name=model,
        max_completion_tokens=max_completion_tokens,
        minimum_activities_per_batch=1
    )
    
    if not batches:
        logger.error(
            f"Profile {profile_id}: Failed to create batches - "
            f"{batch_metadata.get('error', 'unknown error')}"
        )
        return {
            "summary": None,
            "highlights": [],
            "keywords": []
        }
    
    logger.info(
        f"Profile {profile_id}: Dynamic batching complete - "
        f"{len(post_texts)} posts → {len(batches)} batches "
        f"(model: {batch_metadata.get('model', model)}, "
        f"context window: {batch_metadata.get('model_context_window', 0)} tokens)"
    )
    
    # If only one batch, process directly (no need for map-reduce)
    if len(batches) == 1:
        return await generate_summary_for_batch(
            profile_id, profile_name, profile_context, batches[0], len(post_texts), is_final=True, model=model
        )
    
    # Map phase: Generate intermediate summaries for each batch
    batch_summaries = []
    all_keywords = []
    all_highlights = []
    
    for idx, batch in enumerate(batches):
        logger.info(f"Profile {profile_id}: Processing batch {idx + 1}/{len(batches)} ({len(batch)} posts)")
        
        batch_result = await generate_summary_for_batch(
            profile_id, profile_name, profile_context, batch, len(batch), is_final=False, model=model
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
    default_combine_system = "You are an expert at synthesizing multiple summaries into one comprehensive analysis. Always respond with valid JSON only."
    system_message, combine_prompt = split_prompt_into_messages(full_combine_prompt, default_combine_system)
    
    # Use dynamic context window management for combine prompt
    adjusted_combine_prompt, adjust_metadata = context_manager.adjust_content_to_fit_context_window(
        content=combine_prompt,
        system_message=system_message,
        model_name=model,
        max_completion_tokens=max_completion_tokens
    )
    
    if adjust_metadata.get("truncated"):
        logger.warning(
            f"Profile {profile_id}: Combine prompt truncated "
            f"({adjust_metadata.get('truncation_ratio', 0):.1%} reduction) "
            f"to fit {model} context window"
        )
    
    try:
        result = await call_openai_with_retry(
            profile_id=profile_id,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": adjusted_combine_prompt}
            ],
            max_tokens=max_completion_tokens,
            max_retries=3,
            initial_delay=1.0,
            validate_summary=True,  # Always validate for combine phase (final summary)
            model=model
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

