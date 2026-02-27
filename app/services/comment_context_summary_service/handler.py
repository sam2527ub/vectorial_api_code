"""Async handler for adding context summaries to LinkedIn comments.json via Groq/AI Gateway."""

import asyncio
import uuid
from typing import Any, Dict, List, Optional

import httpx
from fastapi import HTTPException

from app import database
from app.config import logger, s3_client, s3_bucket
from app.utils.s3_utils import get_s3_key_for_audience, fetch_json_from_s3, upload_json_to_s3
from app.services.ai_gateway_service import ai_gateway
from app.services.comment_context_summary_service.config import CommentContextSummaryConfig
from app.services.comment_context_summary_service.utils import build_context_for_comment
from app.services.comment_context_summary_service.repositories.comment_context_summary_job_repository import (
    create_comment_context_summary_job,
    get_comment_context_summary_job,
    update_comment_context_summary_job,
)
from app.services.user_profile_summarization_service.utils import get_base_url


class CommentContextSummaryAsyncHandler:
    """Trigger async comment context summaries, process chunks, and expose status."""

    def __init__(self, config: Optional[CommentContextSummaryConfig] = None) -> None:
        self.config = config or CommentContextSummaryConfig()

    def _ensure_prerequisites(self) -> None:
        if not database.is_audience_db_available():
            raise HTTPException(status_code=503, detail="Audience database not available")
        if not s3_client or not s3_bucket:
            raise HTTPException(status_code=503, detail="S3 is not configured.")

    def trigger_async_comment_context_summary(
        self,
        audience_room_id: str,
        enterprise_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a comment context summary job and return response."""
        self._ensure_prerequisites()
        room = database.find_audience_room_by_id(
            audience_room_id, include_profiles=False, enterprise_name=enterprise_name
        )
        if not room:
            raise HTTPException(status_code=404, detail=f"Audience room {audience_room_id} not found")

        job_id = str(uuid.uuid4())
        job = create_comment_context_summary_job(
            job_id=job_id,
            audience_room_id=audience_room_id,
            enterprise_name=enterprise_name,
        )
        logger.info(
            "[CommentContextSummaryJob %s] Created for room %s (enterprise=%s)",
            job_id,
            audience_room_id,
            enterprise_name,
        )
        return {
            "job_id": job["job_id"],
            "status": job["status"],
            "audience_room_id": job["audience_room_id"],
            "message": "Comment context summary job started. Use async status endpoint to check progress.",
        }

    async def _summarize_context(
        self,
        profile_id: str,
        comment_id: str,
        context: Dict[str, Optional[str]],
    ) -> str:
        """Call AI Gateway (Groq via gateway or direct) to get JSON {summary: str}."""
        post_body = context.get("post_body") or ""
        parent_body = context.get("parent_comment_body")
        parent_text = parent_body if parent_body else "Direct reply to the post"

        prompt = (
            "You are summarizing LinkedIn comment context.\n"
            "Return STRICTLY a JSON object with a single key `summary`.\n"
            "The value must be a 1–2 sentence summary of the context, "
            "capturing the main topic, what is being discussed, and what the commenter is responding to.\n\n"
            f"Post body:\n{post_body}\n\n"
            f"Parent comment (or description of what is being replied to):\n{parent_text}\n"
        )

        messages = [
            {"role": "system", "content": "You return only valid JSON in the format {\"summary\": \"...\"}."},
            {"role": "user", "content": prompt},
        ]

        # Use Groq via AI Gateway with comment-context-specific rotation; when gateway
        # is disabled, DirectApiClient.call_groq is used as a fallback.
        result = await ai_gateway.call_via_gateway(
            context_id=f"{profile_id}:{comment_id}",
            messages=messages,
            max_tokens=200,
            model=None,
            default_model=None,
            fallback_models=None,
            config_default_attr="comment_context_summary_default",
            config_fallbacks_attr="comment_context_summary_fallbacks",
            hardcoded_default=self.config.groq_model,
            validate_summary=False,
            return_text=False,
        )

        if not isinstance(result, dict):
            logger.warning("Unexpected Groq result type for comment %s: %r", comment_id, type(result))
            raise ValueError("Groq result was not a JSON object")

        summary = (result.get("summary") or "").strip()
        if not summary:
            raise ValueError("Groq returned empty summary")
        return summary

    async def _process_profile_comments(
        self,
        profile,
        room_id: str,
        enterprise_name: Optional[str],
        source: Optional[str],
    ) -> Dict[str, int]:
        """Process up to N comments for a single profile."""
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

        comments_to_enrich = comments[: self.config.comments_per_profile]
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

        semaphore = asyncio.Semaphore(self.config.max_concurrent_comments)

        async def _process_single_comment(idx: int, comment: Dict[str, Any]) -> None:
            nonlocal summarized, skipped, errors, processed
            async with semaphore:
                comment_id = comment.get("comment_url") or comment.get("id") or str(idx)

                # Idempotency: if context_summary already present, skip
                existing = (comment.get("context_summary") or "").strip()
                if existing:
                    skipped += 1
                    processed += 1
                    return

                context = build_context_for_comment(comment, self.config)
                if not context:
                    skipped += 1
                    processed += 1
                    return

                try:
                    summary = await self._summarize_context(str(profile_id), str(comment_id), context)
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
            _process_single_comment(idx, comments_to_enrich[idx])
            for idx in range(len(comments_to_enrich))
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=False)

        # Persist changes (full comments list, not just sliced subset)
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

    async def process_comment_context_summary_chunks(
        self,
        job_id: str,
        audience_room_id: str,
        enterprise_name: Optional[str],
        chunk_size_profiles: int,
        start_chunk: int = 0,
        base_url: Optional[str] = None,
    ) -> None:
        """
        Process up to config.chunks_per_api_call chunks, then self-trigger next batch via HTTP.
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

            if start_chunk == 0:
                room = database.find_audience_room_by_id(
                    audience_room_id,
                    include_profiles=True,
                    enterprise_name=enterprise_name,
                )
                if not room:
                    update_comment_context_summary_job(
                        job_id,
                        enterprise_name,
                        status="FAILED",
                        error=f"Audience room {audience_room_id} not found",
                    )
                    return

                all_profiles = room.profiles or []
                total_profiles = len(all_profiles)
                total_chunks = (total_profiles + chunk_size_profiles - 1) // chunk_size_profiles

                if total_profiles == 0:
                    update_comment_context_summary_job(
                        job_id,
                        enterprise_name,
                        status="COMPLETED",
                        total_profiles=0,
                        total_chunks=0,
                    )
                    return

                update_comment_context_summary_job(
                    job_id,
                    enterprise_name,
                    total_profiles=total_profiles,
                    total_chunks=total_chunks,
                    status="PROCESSING",
                )
                logger.info(
                    "[CommentContextSummaryJob %s] Processing %s profiles in %s chunks",
                    job_id,
                    total_profiles,
                    total_chunks,
                )
            else:
                job = get_comment_context_summary_job(job_id, enterprise_name)
                total_profiles = job["total_profiles"]
                total_chunks = job["total_chunks"]
                room = database.find_audience_room_by_id(
                    audience_room_id,
                    include_profiles=True,
                    enterprise_name=enterprise_name,
                )
                if not room:
                    update_comment_context_summary_job(
                        job_id,
                        enterprise_name,
                        status="FAILED",
                        error=f"Audience room {audience_room_id} not found",
                    )
                    return
                all_profiles = room.profiles or []

            total_comments = job.get("total_comments", 0)
            processed_comments = job.get("processed_comments", 0)
            summarized_comments = job.get("summarized_comments", 0)
            skipped_comments = job.get("skipped_comments", 0)
            error_count = job.get("error_count", 0)
            processed_profiles = job.get("processed_profiles", 0)

            # Room source for S3 audience type
            source = getattr(room, "source", None)

            chunks_to_process = min(
                self.config.chunks_per_api_call,
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

                semaphore = asyncio.Semaphore(self.config.max_concurrent_profiles)

                async def _rate_limited_process(profile) -> Dict[str, int]:
                    async with semaphore:
                        result = await self._process_profile_comments(
                            profile=profile,
                            room_id=audience_room_id,
                            enterprise_name=enterprise_name,
                            source=source,
                        )
                        await asyncio.sleep(0.2)
                        return result

                tasks = [_rate_limited_process(p) for p in profiles_chunk]
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
                logger.info(
                    "[CommentContextSummaryJob %s] Completed chunks %s-%s, triggering next batch starting at chunk %s",
                    job_id,
                    start_chunk,
                    start_chunk + chunks_to_process - 1,
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
                            "chunkSize": chunk_size_profiles,
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
            else:
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

    def get_job_status(
        self,
        job_id: str,
        audience_room_id: str,
        enterprise_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return job status payload for polling."""
        job = get_comment_context_summary_job(job_id, enterprise_name)
        if not job or job["audience_room_id"] != audience_room_id:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
        return job

