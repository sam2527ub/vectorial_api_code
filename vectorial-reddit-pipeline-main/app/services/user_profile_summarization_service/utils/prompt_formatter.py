"""Prompt formatting utilities - formats prompts for different summary generation stages."""
from typing import Tuple
from app.services.user_profile_summarization_service.prompts import (
    reddit_profile_posts_summary_prompt,
    reddit_profile_posts_batch_summary_prompt,
    reddit_combine_batch_summaries_prompt
)
from app.utils.message_utils import split_prompt_into_messages


class PromptProcessor:
    """Handles prompt formatting and message preparation."""
    
    def format_single_batch_prompt(
        self,
        profile_context: str,
        total_activities: int,
        text_for_analysis: str,
        profile_name: str
    ) -> Tuple[str, str]:
        """
        Format prompt for single batch summary.
        """
        full_prompt = reddit_profile_posts_summary_prompt.format(
            profile_context=profile_context,
            total_activities=total_activities,
            text_for_analysis=text_for_analysis,
            profile_name=profile_name
        )
        return split_prompt_into_messages(full_prompt)
    
    def format_batch_summary_prompt(
        self,
        profile_context: str,
        total_activities: int,
        text_for_analysis: str
    ) -> Tuple[str, str]:
        """
        Format prompt for batch summary (intermediate).
        """
        full_prompt = reddit_profile_posts_batch_summary_prompt.format(
            profile_context=profile_context,
            total_activities=total_activities,
            text_for_analysis=text_for_analysis
        )
        return split_prompt_into_messages(full_prompt)
    
    def format_combine_summaries_prompt(
        self,
        profile_context: str,
        total_activities: int,
        batch_count: int,
        combined_batch_summaries_text: str,
        profile_name: str
    ) -> Tuple[str, str]:
        """
        Format prompt for combining batch summaries.
        """
        full_prompt = reddit_combine_batch_summaries_prompt.format(
            profile_context=profile_context,
            total_activities=total_activities,
            batch_count=batch_count,
            combined_batch_summaries_text=combined_batch_summaries_text,
            profile_name=profile_name
        )
        return split_prompt_into_messages(full_prompt)
    
    def prepare_system_message_for_batching(
        self,
        profile_context: str,
        total_activities: int
    ) -> str:
        """
        Prepare system message for batch calculation.
        """
        full_prompt = reddit_profile_posts_batch_summary_prompt.format(
            profile_context=profile_context,
            total_activities=total_activities,
            text_for_analysis=""
        )
        system_message, _ = split_prompt_into_messages(full_prompt)
        return system_message
