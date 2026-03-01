"""Processes chunks of profiles for comment context summary and triggers next batch via HTTP."""

import asyncio
from typing import Dict, Optional

from app.config import logger
from app.services.comment_context_summary_service.comment_context_summary_chunk_processor_http_and_status import (
    mark_comment_context_summary_job_completed,
    trigger_next_comment_context_summary_chunk_batch,
)
from app.services.comment_context_summary_service.comment_context_summary_chunk_processor_room_resolver import (
    resolve_room_and_chunk_info_for_job,
)
from app.services.comment_context_summary_service.config import CommentContextSummaryConfig
from app.services.comment_context_summary_service.profile_comments_context_processor import (
    process_profile_comments_for_context_summary,
)
from app.services.comment_context_summary_service.repositories.comment_context_summary_job_repository import (
    get_comment_context_summary_job,
    update_comment_context_summary_job,
)


async def process_comment_context_summary_chunks(
    job_id: str,
    audience_room_id: str,
    enterprise_name: Optional[str],
    chunk_size_profiles: int,
    config: CommentContextSummaryConfig,
    start_chunk: int = 0,
    base_url: Optional[str] = None,
) -> None:
    """
    Process up to config.chunks_per_api_call chunks of profiles, then self-trigger next batch via HTTP.
    Updates job in DB and triggers next chunk endpoint when more chunks remain.
    """
    try:
        logger.info(
            "[CommentContextSummaryJob %s] Processing chunks starting from chunk %s",
            job_id,
            start_chunk,
        )

        job = get_comment_context_summary_job(job_id, enterprise_name)
        if not job:
            logger.error("[CommentContextSummaryJob %s] Job not found in database", job_id)
            return

        if job["status"] not in ("PENDING", "PROCESSING"):
            logger.info(
                "[CommentContextSummaryJob %s] Job already completed/failed: %s",
                job_id,
                job["status"],
            )
            return

        room, all_profiles, total_profiles, total_chunks = resolve_room_and_chunk_info_for_job(
            job_id, audience_room_id, enterprise_name, start_chunk, chunk_size_profiles, job
        )
        if room is None:
            return
        if all_profiles is None:
            return

        source = getattr(room, "source", None)
        total_comments = job.get("total_comments", 0)
        processed_comments = job.get("processed_comments", 0)
        summarized_comments = job.get("summarized_comments", 0)
        skipped_comments = job.get("skipped_comments", 0)
        error_count = job.get("error_count", 0)
        processed_profiles = job.get("processed_profiles", 0)

        chunks_to_process = min(
            config.chunks_per_api_call,
            total_chunks - start_chunk,
        )

        for chunk_offset in range(chunks_to_process):
            chunk_num = start_chunk + chunk_offset
            if chunk_num >= total_chunks:
                break

            chunk_start = chunk_num * chunk_size_profiles
            chunk_end = min(chunk_start + chunk_size_profiles, total_profiles)
            profiles_chunk = all_profiles[chunk_start:chunk_end]

            logger.info(
                "[CommentContextSummaryJob %s] Processing chunk %s/%s (%s profiles)",
                job_id,
                chunk_num + 1,
                total_chunks,
                len(profiles_chunk),
            )
            update_comment_context_summary_job(
                job_id,
                enterprise_name,
                current_chunk=chunk_num + 1,
            )

            semaphore = asyncio.Semaphore(config.max_concurrent_profiles)

            async def rate_limited_process(profile) -> Dict[str, int]:
                async with semaphore:
                    result = await process_profile_comments_for_context_summary(
                        profile=profile,
                        room_id=audience_room_id,
                        enterprise_name=enterprise_name,
                        source=source,
                        config=config,
                    )
                    await asyncio.sleep(0.2)
                    return result

            tasks = [rate_limited_process(p) for p in profiles_chunk]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for idx, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(
                        "[CommentContextSummaryJob %s] Error processing profile %s: %s",
                        job_id,
                        profiles_chunk[idx].id,
                        result,
                    )
                    error_count += 1
                    processed_profiles += 1
                else:
                    total_comments += result.get("total_comments", 0)
                    processed_comments += result.get("processed_comments", 0)
                    summarized_comments += result.get("summarized_comments", 0)
                    skipped_comments += result.get("skipped_comments", 0)
                    error_count += result.get("error_count", 0)
                    processed_profiles += 1

            update_comment_context_summary_job(
                job_id,
                enterprise_name,
                processed_profiles=processed_profiles,
                total_comments=total_comments,
                processed_comments=processed_comments,
                summarized_comments=summarized_comments,
                skipped_comments=skipped_comments,
                error_count=error_count,
            )
            await asyncio.sleep(0.5)

        next_chunk = start_chunk + chunks_to_process
        if next_chunk < total_chunks:
            await trigger_next_comment_context_summary_chunk_batch(
                job_id, audience_room_id, enterprise_name,
                chunk_size_profiles, next_chunk, base_url,
            )
        else:
            mark_comment_context_summary_job_completed(
                job_id, enterprise_name,
                total_comments, processed_comments,
                summarized_comments, skipped_comments, error_count,
            )
    except Exception as e:
        logger.error(
            "[CommentContextSummaryJob %s] Failed: %s",
            job_id,
            e,
            exc_info=True,
        )
        update_comment_context_summary_job(
            job_id,
            enterprise_name,
            status="FAILED",
            error=str(e),
        )
