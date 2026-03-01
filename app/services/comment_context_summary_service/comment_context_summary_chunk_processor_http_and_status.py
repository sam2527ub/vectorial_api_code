"""Triggers next chunk via HTTP and marks job completed for comment context summary."""

from typing import Any, Dict, Optional

import httpx

from app.config import logger
from app.services.comment_context_summary_service.repositories.comment_context_summary_job_repository import (
    update_comment_context_summary_job,
)
from app.services.user_profile_summarization_service.utils import get_base_url


async def trigger_next_comment_context_summary_chunk_batch(
    job_id: str,
    audience_room_id: str,
    enterprise_name: Optional[str],
    chunk_size_profiles: int,
    next_chunk: int,
    base_url: Optional[str],
) -> None:
    """Trigger the process endpoint for the next chunk via HTTP."""
    logger.info(
        "[CommentContextSummaryJob %s] Completed chunks, triggering next batch starting at chunk %s",
        job_id,
        next_chunk,
    )
    api_base_url = base_url or get_base_url()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            trigger_url = (
                f"{api_base_url}/api/v1/audience-rooms/"
                f"{audience_room_id}/comment-context-summary/async/process"
            )
            params: Dict[str, Any] = {
                "jobId": job_id,
                "startChunk": next_chunk,
                "chunkSizeProfiles": chunk_size_profiles,
            }
            if enterprise_name:
                params["enterpriseName"] = enterprise_name
            response = await client.post(trigger_url, params=params)
            response.raise_for_status()
        logger.info(
            "[CommentContextSummaryJob %s] Successfully triggered next batch (chunk %s)",
            job_id,
            next_chunk,
        )
    except Exception as e:
        logger.error(
            "[CommentContextSummaryJob %s] Failed to trigger next batch: %s",
            job_id,
            e,
            exc_info=True,
        )


def mark_comment_context_summary_job_completed(
    job_id: str,
    enterprise_name: Optional[str],
    total_comments: int,
    processed_comments: int,
    summarized_comments: int,
    skipped_comments: int,
    error_count: int,
) -> None:
    """Mark the comment context summary job as COMPLETED and persist final counts."""
    logger.info(
        "[CommentContextSummaryJob %s] Completed all chunks. "
        "Summarized=%s, skipped=%s, errors=%s, total_comments=%s",
        job_id,
        summarized_comments,
        skipped_comments,
        error_count,
        total_comments,
    )
    update_comment_context_summary_job(
        job_id,
        enterprise_name,
        status="COMPLETED",
        total_comments=total_comments,
        processed_comments=processed_comments,
        summarized_comments=summarized_comments,
        skipped_comments=skipped_comments,
        error_count=error_count,
    )
