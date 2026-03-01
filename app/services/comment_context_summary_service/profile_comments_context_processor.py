"""Processes one profile's comments: load from S3, add context summaries via AI, upload back."""

import asyncio
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from app.config import logger
from app.utils.s3_utils import (
    fetch_json_from_s3,
    get_s3_key_for_audience,
    upload_json_to_s3,
)

from app.services.comment_context_summary_service.comment_context_summary_ai_summarizer import (
    summarize_comment_context_via_ai,
)
from app.services.comment_context_summary_service.config import CommentContextSummaryConfig
from app.services.comment_context_summary_service.utils import build_context_for_comment


async def process_profile_comments_for_context_summary(
    profile: Any,
    room_id: str,
    enterprise_name: Optional[str],
    source: Optional[str],
    config: CommentContextSummaryConfig,
) -> Dict[str, int]:
    """
    Process up to config.comments_per_profile comments for a single profile.
    Loads comments from S3, enriches with context_summary via AI, uploads back.
    Returns counts: total_comments, processed_comments, summarized_comments, skipped_comments, error_count.
    """
    profile_id = profile.id
    key = get_s3_key_for_audience(
        room_id,
        f"profiles/{profile_id}/comment.json",
        enterprise_name,
        source,
    )
    logger.debug(
        "[CommentContextSummary] Profile %s: loading comments from S3 key %s",
        profile_id,
        key,
    )

    try:
        data = fetch_json_from_s3(key)
    except HTTPException as e:
        logger.warning(
            "[CommentContextSummary] Profile %s: failed to load comments.json (%s)",
            profile_id,
            e.detail,
        )
        return {
            "total_comments": 0,
            "processed_comments": 0,
            "summarized_comments": 0,
            "skipped_comments": 0,
            "error_count": 1,
        }

    comments: List[Dict[str, Any]] = data.get("comments") or []
    if not isinstance(comments, list):
        logger.warning(
            "[CommentContextSummary] Profile %s: comments payload is not a list",
            profile_id,
        )
        return {
            "total_comments": 0,
            "processed_comments": 0,
            "summarized_comments": 0,
            "skipped_comments": 0,
            "error_count": 1,
        }

    comments_to_enrich = comments[: config.comments_per_profile]
    total = len(comments_to_enrich)
    if total == 0:
        return {
            "total_comments": 0,
            "processed_comments": 0,
            "summarized_comments": 0,
            "skipped_comments": 0,
            "error_count": 0,
        }

    summarized = 0
    skipped = 0
    errors = 0
    processed = 0
    semaphore = asyncio.Semaphore(config.max_concurrent_comments)

    async def process_single_comment(idx: int, comment: Dict[str, Any]) -> None:
        nonlocal summarized, skipped, errors, processed
        async with semaphore:
            comment_id = comment.get("comment_url") or comment.get("id") or str(idx)

            existing = (comment.get("context_summary") or "").strip()
            if existing:
                skipped += 1
                processed += 1
                return

            context = build_context_for_comment(comment, config)
            if not context:
                skipped += 1
                processed += 1
                return

            try:
                summary = await summarize_comment_context_via_ai(
                    str(profile_id), str(comment_id), context, config
                )
                comment["context_summary"] = summary
                summarized += 1
            except Exception as e:
                logger.error(
                    "[CommentContextSummary] Profile %s comment %s: summary error: %s",
                    profile_id,
                    comment_id,
                    e,
                )
                errors += 1
            finally:
                processed += 1

    tasks = [
        process_single_comment(idx, comments_to_enrich[idx])
        for idx in range(len(comments_to_enrich))
    ]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=False)

    try:
        upload_json_to_s3(key, {"comments": comments})
    except HTTPException as e:
        logger.error(
            "[CommentContextSummary] Profile %s: failed to upload updated comments.json: %s",
            profile_id,
            e.detail,
        )
        errors += 1

    return {
        "total_comments": total,
        "processed_comments": processed,
        "summarized_comments": summarized,
        "skipped_comments": skipped,
        "error_count": errors,
    }
