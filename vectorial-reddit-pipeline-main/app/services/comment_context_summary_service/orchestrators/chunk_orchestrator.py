"""Chunk orchestration for comment context summary."""
import asyncio
from typing import Optional
import httpx
from app.config import logger


class ChunkOrchestrator:
    """Handles chunk triggering and completion."""
    
    @staticmethod
    async def trigger_next_chunk(
        base_url: str,
        job_id: str,
        audience_room_id: str,
        enterprise_name: Optional[str],
        start_profile_index: int,
        chunk_size: int
    ) -> None:
        """Self-trigger next chunk via HTTP."""
        url = f"{base_url.rstrip('/')}/api/v1/comment-context-summary/process"
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
            logger.debug(f"[CommentContextSummary] Triggered next chunk at index {start_profile_index}")
        except httpx.ReadTimeout:
            logger.info(f"[CommentContextSummary] Next chunk triggered (read timeout - expected)")
        except Exception as e:
            logger.error(f"[CommentContextSummary] Failed to trigger next chunk: {e}", exc_info=True)
    
    @staticmethod
    async def handle_chunk_completion(
        base_url: str,
        job_id: str,
        audience_room_id: str,
        enterprise_name: Optional[str],
        start_profile_index: int,
        chunk_size: int,
        total_profiles: int
    ) -> None:
        """Handle chunk completion - trigger next or complete job."""
        from app.services.comment_context_summary_service.job_status_manager import JobStatusManager
        
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
                        "status": "success",
                        "total_profiles": job.total_profiles or 0,
                        "processed_profiles": job.processed_profiles or 0,
                        "enriched_comments": job.enriched_comments or 0,
                        "skipped_comments": job.skipped_comments or 0,
                        "failed_comments": job.failed_comments or 0,
                    },
                    enriched_comments=job.enriched_comments or 0,
                    skipped_comments=job.skipped_comments or 0,
                    failed_comments=job.failed_comments or 0,
                    total_profiles=job.total_profiles or 0,
                    processed_profiles=job.processed_profiles or 0,
                    total_comments=job.total_comments or 0,
                    enterprise_name=enterprise_name
                )
