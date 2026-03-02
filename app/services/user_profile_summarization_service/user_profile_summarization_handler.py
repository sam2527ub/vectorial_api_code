"""Handler for User Profile Summarization: trigger async summaries, process chunks, status."""
import asyncio
import uuid
from typing import Any, Dict, Optional

import httpx
from fastapi import HTTPException

from app.config import logger
from app.utils.helpers import ensure_db_available
from app import database
from app.services.user_profile_summarization_service.profile_summary_processor import process_profile_summary
from app.services.user_profile_summarization_service.config import (
    UserProfileSummarizationConfig,
    CHUNK_SIZE,
    CHUNKS_PER_API_CALL,
    MAX_CONCURRENT,
)
from app.services.user_profile_summarization_service.repositories import (
    create_summaries_job,
    get_summaries_job,
    update_summaries_job,
)
from app.services.user_profile_summarization_service.utils import get_base_url


class UserProfileSummarizationHandler:
    """Trigger async profile summaries, process chunks, and expose status."""

    def __init__(self, config: Optional[UserProfileSummarizationConfig] = None):
        self.config = config or UserProfileSummarizationConfig()
        self.chunk_size = self.config.chunk_size
        self.chunks_per_api_call = self.config.chunks_per_api_call
        self.max_concurrent = self.config.max_concurrent

    def trigger_async_summaries(
        self,
        audience_room_id: str,
        enterprise_name: Optional[str] = None,
        chunk_size: Optional[int] = None,
        task_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a summaries job and return response. Caller must add background task
        to run process_summaries_chunks(job_id, audience_room_id, enterprise_name, chunk_size, 0, base_url).
        """
        ensure_db_available("audience")
        audience_room = database.find_audience_room_by_id(
            audience_room_id, include_profiles=False, enterprise_name=enterprise_name
        )
        if not audience_room:
            raise HTTPException(status_code=404, detail=f"Audience room {audience_room_id} not found")

        size = chunk_size if chunk_size is not None else self.chunk_size
        job_id = str(uuid.uuid4())
        job = create_summaries_job(
            job_id=job_id,
            audience_room_id=audience_room_id,
            task_token=task_token,
            enterprise_name=enterprise_name,
        )
        return {
            "job_id": job_id,
            "status": "PENDING",
            "audience_room_id": audience_room_id,
            "message": "Profile summaries job started. Use /generate-summaries/async/status/{job_id} to check progress.",
        }

    async def process_summaries_chunks(
        self,
        job_id: str,
        audience_room_id: str,
        enterprise_name: Optional[str],
        chunk_size: int,
        start_chunk: int = 0,
        base_url: Optional[str] = None,
    ) -> None:
        """
        Process up to CHUNKS_PER_API_CALL chunks, then self-trigger next batch via HTTP.
        """
        try:
            logger.info(f"[SummariesJob {job_id}] Processing chunks starting from chunk {start_chunk}")

            job = get_summaries_job(job_id, enterprise_name)
            if not job:
                logger.error(f"[SummariesJob {job_id}] Job not found in database")
                return

            if job["status"] not in ["PENDING", "PROCESSING"]:
                logger.info(f"[SummariesJob {job_id}] Job already completed/failed: {job['status']}")
                return

            if start_chunk == 0:
                audience_room = database.find_audience_room_by_id(
                    audience_room_id,
                    include_profiles=True,
                    enterprise_name=enterprise_name,
                )
                if not audience_room:
                    update_summaries_job(
                        job_id, enterprise_name, status="FAILED",
                        error=f"Audience room {audience_room_id} not found",
                    )
                    return

                all_profiles = audience_room.profiles or []
                total_profiles = len(all_profiles)
                total_chunks = (total_profiles + chunk_size - 1) // chunk_size

                if total_profiles == 0:
                    update_summaries_job(job_id, enterprise_name, status="COMPLETED", total_profiles=0)
                    return

                update_summaries_job(
                    job_id, enterprise_name,
                    total_profiles=total_profiles,
                    total_chunks=total_chunks,
                    status="PROCESSING",
                )
                logger.info(f"[SummariesJob {job_id}] Processing {total_profiles} profiles in {total_chunks} chunks")
            else:
                job = get_summaries_job(job_id, enterprise_name)
                total_profiles = job["total_profiles"]
                total_chunks = job["total_chunks"]
                audience_room = database.find_audience_room_by_id(
                    audience_room_id,
                    include_profiles=True,
                    enterprise_name=enterprise_name,
                )
                if not audience_room:
                    update_summaries_job(
                        job_id, enterprise_name, status="FAILED",
                        error=f"Audience room {audience_room_id} not found",
                    )
                    return
                all_profiles = audience_room.profiles or []

            total_success = job.get("success_count", 0)
            total_skipped = job.get("skipped_count", 0)
            total_errors = job.get("error_count", 0)
            processed = job.get("processed_profiles", 0)

            chunks_to_process = min(self.chunks_per_api_call, total_chunks - start_chunk)

            semaphore = asyncio.Semaphore(self.max_concurrent)

            for chunk_offset in range(chunks_to_process):
                chunk_num = start_chunk + chunk_offset
                if chunk_num >= total_chunks:
                    break

                chunk_start = chunk_num * chunk_size
                chunk_end = min(chunk_start + chunk_size, total_profiles)
                profiles_chunk = all_profiles[chunk_start:chunk_end]

                logger.info(
                    f"[SummariesJob {job_id}] Processing chunk {chunk_num + 1}/{total_chunks} ({len(profiles_chunk)} profiles)"
                )
                update_summaries_job(job_id, enterprise_name, current_chunk=chunk_num + 1)

                async def rate_limited_process(profile):
                    async with semaphore:
                        result = await process_profile_summary(
                            profile, audience_room_id, enterprise_name=enterprise_name
                        )
                        await asyncio.sleep(1.0)
                        return result

                tasks = [rate_limited_process(p) for p in profiles_chunk]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                for idx, result in enumerate(results):
                    if isinstance(result, Exception):
                        logger.error(
                            f"[SummariesJob {job_id}] Error processing profile {profiles_chunk[idx].id}: {result}"
                        )
                        total_errors += 1
                    elif result["status"] == "success":
                        total_success += 1
                    elif result["status"] == "skipped":
                        total_skipped += 1
                    else:
                        total_errors += 1
                    processed += 1

                update_summaries_job(
                    job_id, enterprise_name,
                    processed_profiles=processed,
                    success_count=total_success,
                    skipped_count=total_skipped,
                    error_count=total_errors,
                )
                logger.info(
                    f"[SummariesJob {job_id}] Chunk {chunk_num + 1} complete: "
                    f"{total_success} success, {total_skipped} skipped, {total_errors} errors"
                )
                await asyncio.sleep(0.5)

            next_chunk = start_chunk + chunks_to_process

            if next_chunk < total_chunks:
                logger.info(
                    f"[SummariesJob {job_id}] Completed chunks {start_chunk}-{start_chunk + chunks_to_process - 1}, "
                    f"triggering next batch starting at chunk {next_chunk}"
                )
                api_base_url = base_url or get_base_url()
                try:
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        trigger_url = (
                            f"{api_base_url}/api/v1/audience-rooms/{audience_room_id}/generate-summaries/async/process"
                        )
                        params = {
                            "jobId": job_id,
                            "startChunk": next_chunk,
                            "chunkSize": chunk_size,
                        }
                        if enterprise_name:
                            params["enterpriseName"] = enterprise_name
                        response = await client.post(trigger_url, params=params)
                        response.raise_for_status()
                        logger.info(f"[SummariesJob {job_id}] Successfully triggered next batch (chunk {next_chunk})")
                except Exception as e:
                    # Log only; do not mark job FAILED (same as comment context summary - trigger is best-effort)
                    logger.error(
                        f"[SummariesJob {job_id}] Failed to trigger next batch: %s",
                        e,
                        exc_info=True,
                    )
            else:
                update_summaries_job(
                    job_id, enterprise_name,
                    status="COMPLETED",
                    processed_profiles=processed,
                    success_count=total_success,
                    skipped_count=total_skipped,
                    error_count=total_errors,
                )
                logger.info(
                    f"[SummariesJob {job_id}] ✅ Completed: {total_success} success, "
                    f"{total_skipped} skipped, {total_errors} errors"
                )

        except Exception as e:
            logger.error(f"[SummariesJob {job_id}] Failed: {str(e)}", exc_info=True)
            update_summaries_job(job_id, enterprise_name, status="FAILED", error=str(e))

    def get_job_status(
        self,
        job_id: str,
        audience_room_id: str,
        enterprise_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return job status dict. Raises HTTPException if not found or room mismatch."""
        job = get_summaries_job(job_id, enterprise_name)
        if not job:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
        if job["audience_room_id"] != audience_room_id:
            raise HTTPException(
                status_code=404,
                detail=f"Job {job_id} not found for audience room {audience_room_id}",
            )
        return {
            "job_id": job["job_id"],
            "status": job["status"],
            "audience_room_id": job["audience_room_id"],
            "total_profiles": job["total_profiles"],
            "processed_profiles": job["processed_profiles"],
            "success_count": job["success_count"],
            "skipped_count": job["skipped_count"],
            "error_count": job["error_count"],
            "current_chunk": job["current_chunk"],
            "total_chunks": job["total_chunks"],
            "error": job["error"],
            "created_at": job["created_at"],
            "updated_at": job["updated_at"],
        }
