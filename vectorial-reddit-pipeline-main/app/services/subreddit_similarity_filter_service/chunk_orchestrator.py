"""Chunk orchestration for subreddit similarity filter."""
import asyncio
from typing import Optional
import httpx
from app.config import logger
from .job_status_manager import JobStatusManager


class ChunkOrchestrator:
    """Handles chunk triggering and completion."""

    @staticmethod
    async def trigger_next_chunk(
        base_url: str,
        job_id: str,
        start_subreddit_index: int,
        chunk_size: int,
        enterprise_name: Optional[str] = None,
    ) -> None:
        """Self-trigger next chunk via HTTP."""
        url = f"{base_url.rstrip('/')}/api/v1/filter/subreddit-similarity/process"
        json_body = {
            "jobId": job_id,
            "startSubredditIndex": start_subreddit_index,
            "chunkSize": chunk_size,
        }
        if enterprise_name is not None:
            json_body["enterpriseName"] = enterprise_name

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(url, json=json_body)
            logger.debug(f"[SubSimilarityJob {job_id}] Triggered next chunk at index {start_subreddit_index}")
        except httpx.ReadTimeout:
            logger.info(f"[SubSimilarityJob {job_id}] Next chunk triggered (read timeout)")
        except Exception as e:
            logger.error(f"[SubSimilarityJob {job_id}] Failed to trigger next chunk: {e}", exc_info=True)
            JobStatusManager.fail_job(
                job_id=job_id, error=f"Failed to trigger next chunk: {e}",
                enterprise_name=enterprise_name,
            )

    @staticmethod
    async def handle_chunk_completion(
        base_url: str,
        job_id: str,
        start_subreddit_index: int,
        chunk_size: int,
        total_subreddits_to_check: int,
        enterprise_name: Optional[str] = None,
    ) -> bool:
        """Handle chunk completion — trigger next or signal done.

        Returns True if there are more chunks to process, False if all done.
        """
        next_start = start_subreddit_index + chunk_size
        if next_start < total_subreddits_to_check:
            await asyncio.sleep(2.0)
            await ChunkOrchestrator.trigger_next_chunk(
                base_url, job_id, next_start, chunk_size, enterprise_name,
            )
            return True
        return False
