"""Generate profile summary from posts (map-reduce batching). Part of User Profile Summarization."""
import asyncio
from typing import Dict, List, Any, Optional

from app.config import logger
from app.utils.message_utils import split_prompt_into_messages
from app.services.ai_gateway_service import ai_gateway
from app.services.dynamic_context_window_management_service import context_manager
from prompts import (
    profile_posts_summary_prompt,
    profile_posts_batch_summary_prompt,
    combine_batch_summaries_prompt,
)


async def generate_summary_for_batch(
    profile_id: str,
    profile_name: str,
    profile_context: str,
    post_texts: List[str],
    total_posts: int,
    is_final: bool = True,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate summary for one batch of posts."""
    text_for_analysis = "\n\n".join(post_texts)
    if is_final:
        full_prompt = profile_posts_summary_prompt.format(
            profile_context=profile_context,
            total_posts=total_posts,
            text_for_analysis=text_for_analysis,
            profile_name=profile_name,
        )
    else:
        full_prompt = profile_posts_batch_summary_prompt.format(
            profile_context=profile_context,
            total_posts=total_posts,
            text_for_analysis=text_for_analysis,
        )
    system_message, user_prompt = split_prompt_into_messages(full_prompt)
    max_completion_tokens = 1000 if not is_final else 1500
    effective_model = model or "openai/gpt-5-mini"
    adjusted_user_prompt, adjust_metadata = context_manager.adjust_content_to_fit_context_window(
        content=user_prompt,
        system_message=system_message,
        model_name=effective_model,
        max_completion_tokens=max_completion_tokens,
    )
    if adjust_metadata.get("truncated"):
        logger.warning(f"Profile {profile_id}: Batch content truncated to fit context")
    try:
        result = await ai_gateway.call_via_gateway(
            context_id=profile_id,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": adjusted_user_prompt},
            ],
            max_tokens=max_completion_tokens,
            model=effective_model,
            default_model="openai/gpt-5-mini",
            fallback_models=["anthropic/claude-sonnet-4.5", "openai/gpt-4o-mini"],
            config_default_attr="profile_summary_default",
            config_fallbacks_attr="profile_summary_fallbacks",
            hardcoded_default="openai/gpt-5-mini",
            validate_summary=is_final,
            return_text=False,
            direct_api_fallback_model=effective_model,
        )
        return {
            "summary": result.get("summary", ""),
            "highlights": result.get("highlights", []),
            "keywords": result.get("keywords", []),
        }
    except Exception as e:
        logger.error(f"Error generating batch summary for profile {profile_id}: {e}")
        return {"summary": None, "highlights": [], "keywords": []}


async def generate_profile_summary_from_posts(
    profile_id: str,
    profile_name: str,
    profile_title: Optional[str],
    profile_company: Optional[str],
    posts: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Generate summary, keywords, highlights for a profile from posts (map-reduce batching)."""
    if not posts:
        return {"summary": None, "highlights": [], "keywords": []}
    post_texts = [p.get("text", "").strip() for p in posts if (p.get("text") or "").strip()]
    if not post_texts:
        return {"summary": None, "highlights": [], "keywords": []}

    if profile_title and profile_company:
        profile_context = f"{profile_name}, who is a {profile_title} at {profile_company}"
    elif profile_company:
        profile_context = f"{profile_name}, who works at {profile_company}"
    else:
        profile_context = profile_name

    model = "openai/gpt-5-mini"
    max_completion_tokens = 1500
    temp_system = "You are an expert at analyzing LinkedIn posts and generating professional summaries. Always respond with valid JSON only."
    batches, batch_metadata = context_manager.calculate_optimal_batch_sizes_for_activities(
        activity_text_list=post_texts,
        system_message=temp_system,
        model_name=model,
        max_completion_tokens=max_completion_tokens,
        minimum_activities_per_batch=1,
    )
    if not batches:
        logger.error(f"Profile {profile_id}: Failed to create batches")
        return {"summary": None, "highlights": [], "keywords": []}

    logger.info(f"Profile {profile_id}: {len(post_texts)} posts → {len(batches)} batches")

    if len(batches) == 1:
        return await generate_summary_for_batch(
            profile_id, profile_name, profile_context, batches[0], len(post_texts), is_final=True, model=model
        )

    batch_summaries = []
    all_keywords = []
    all_highlights = []
    for idx, batch in enumerate(batches):
        logger.info(f"Profile {profile_id}: Batch {idx + 1}/{len(batches)} ({len(batch)} posts)")
        batch_result = await generate_summary_for_batch(
            profile_id, profile_name, profile_context, batch, len(batch), is_final=False, model=model
        )
        if batch_result.get("summary"):
            batch_summaries.append(batch_result["summary"])
        if batch_result.get("keywords"):
            all_keywords.extend(batch_result["keywords"])
        if batch_result.get("highlights"):
            all_highlights.extend(batch_result["highlights"])
        await asyncio.sleep(0.8)

    if not batch_summaries:
        return {"summary": None, "highlights": [], "keywords": []}

    unique_keywords = list(dict.fromkeys(all_keywords))[:15]
    unique_highlights = list(dict.fromkeys(all_highlights))[:6]
    combined_summaries = "\n\n".join([f"Batch {i+1}: {s}" for i, s in enumerate(batch_summaries)])

    full_combine_prompt = combine_batch_summaries_prompt.format(
        profile_context=profile_context,
        total_posts=len(post_texts),
        batch_count=len(batches),
        combined_summaries=combined_summaries,
        profile_name=profile_name,
    )
    default_combine_system = "You are an expert at synthesizing multiple summaries into one comprehensive analysis. Always respond with valid JSON only."
    system_message, combine_prompt = split_prompt_into_messages(full_combine_prompt, default_combine_system)
    adjusted_combine_prompt, _ = context_manager.adjust_content_to_fit_context_window(
        content=combine_prompt,
        system_message=system_message,
        model_name=model,
        max_completion_tokens=max_completion_tokens,
    )
    try:
        result = await ai_gateway.call_via_gateway(
            context_id=profile_id,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": adjusted_combine_prompt},
            ],
            max_tokens=max_completion_tokens,
            model=model,
            default_model="openai/gpt-5-mini",
            fallback_models=["anthropic/claude-sonnet-4.5", "openai/gpt-4o-mini"],
            config_default_attr="profile_summary_default",
            config_fallbacks_attr="profile_summary_fallbacks",
            hardcoded_default="openai/gpt-5-mini",
            validate_summary=True,
            return_text=False,
            direct_api_fallback_model=model,
        )
        return {
            "summary": result.get("summary", ""),
            "highlights": result.get("highlights", unique_highlights),
            "keywords": result.get("keywords", unique_keywords),
        }
    except Exception as e:
        logger.error(f"Error combining batch summaries for profile {profile_id}: {e}")
        fallback_summary = batch_summaries[0] if batch_summaries else None
        return {
            "summary": fallback_summary,
            "highlights": unique_highlights,
            "keywords": unique_keywords,
        }
