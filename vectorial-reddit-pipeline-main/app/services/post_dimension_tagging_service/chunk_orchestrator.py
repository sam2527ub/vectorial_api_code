"""Chunk orchestration for dimension tagging."""
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
        url = f"{base_url.rstrip('/')}/api/v1/posts/dimension-tagging/process"
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
            logger.debug(f"[DimensionTaggingJob {job_id}] Triggered next chunk at index {start_profile_index}")
        except httpx.ReadTimeout:
            logger.info(f"[DimensionTaggingJob {job_id}] Next chunk triggered (read timeout)")
        except Exception as e:
            logger.error(f"[DimensionTaggingJob {job_id}] Failed to trigger next chunk: {e}", exc_info=True)
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
            await asyncio.sleep(5.0)
            await ChunkOrchestrator.trigger_next_chunk(
                base_url, job_id, audience_room_id, enterprise_name, next_start, chunk_size
            )
        else:
            job = JobStatusManager.get_job(job_id, enterprise_name)
            if job:
                JobStatusManager.complete_job(
                    job_id=job_id, result={
                        "status": "success", "total_posts": job.total_posts or 0,
                        "tagged_posts": job.tagged_posts or 0, "failed_posts": job.failed_posts or 0,
                        "updated_s3_urls": []
                    },
                    tagged_posts=job.tagged_posts or 0, failed_posts=job.failed_posts or 0,
                    total_posts=job.total_posts or 0, processed_posts=job.processed_posts or 0,
                    enterprise_name=enterprise_name
                )
