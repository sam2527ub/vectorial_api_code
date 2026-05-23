"""Chunk orchestration for comment dimension tagging."""
import asyncio
from typing import Optional
import httpx
from app.config import logger
from .job_status_manager import JobStatusManager


class ChunkOrchestrator:
    """Handles chunk triggering and completion."""
    
    @staticmethod
    async def trigger_next_chunk(
        base_url: str, job_id: str, audience_room_id: str,
        enterprise_name: Optional[str], start_profile_index: int, chunk_size: int
    ) -> None:
        """Self-trigger next chunk via HTTP."""
        url = f"{base_url.rstrip('/')}/api/v1/comments/dimension-tagging/process"
        json_body = {
            "jobId": job_id,
            "audienceRoomId": audience_room_id,
            "startProfileIndex": start_profile_index,
            "chunkSize": chunk_size,
        }
        if enterprise_name is not None:
            json_body["enterpriseName"] = enterprise_name

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(url, json=json_body)
            logger.debug(f"[CommentDimensionTaggingJob {job_id}] Triggered next chunk at index {start_profile_index}")
        except httpx.ReadTimeout:
            logger.info(f"[CommentDimensionTaggingJob {job_id}] Next chunk triggered (read timeout - expected)")
        except Exception as e:
            logger.error(f"[CommentDimensionTaggingJob {job_id}] Failed to trigger next chunk: {e}", exc_info=True)
            JobStatusManager.fail_job(
                job_id=job_id, error=f"Failed to trigger next chunk: {e}", enterprise_name=enterprise_name
            )
    
    @staticmethod
    async def handle_chunk_completion(
        base_url: str, job_id: str, audience_room_id: str,
        enterprise_name: Optional[str], start_profile_index: int,
        chunk_size: int, total_profiles: int
    ) -> None:
        """Handle chunk completion - trigger next or complete job."""
        next_start = start_profile_index + chunk_size
        
        if next_start < total_profiles:
            await asyncio.sleep(0.5)
            await ChunkOrchestrator.trigger_next_chunk(
                base_url, job_id, audience_room_id, enterprise_name, next_start, chunk_size
            )
        else:
            job = JobStatusManager.get_job(job_id, enterprise_name)
            if job:
                JobStatusManager.complete_job(
                    job_id=job_id, result={
                        "status": "success", "total_comments": job.total_comments or 0,
                        "tagged_comments": job.tagged_comments or 0, "failed_comments": job.failed_comments or 0,
                    },
                    tagged_comments=job.tagged_comments or 0, failed_comments=job.failed_comments or 0,
                    total_comments=job.total_comments or 0, processed_comments=job.processed_comments or 0,
                    skipped_comments=job.skipped_comments or 0,
                    enterprise_name=enterprise_name
                )