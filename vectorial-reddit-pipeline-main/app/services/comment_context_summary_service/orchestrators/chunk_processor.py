"""Chunk processing for comment context summary."""
import asyncio
from typing import Dict, Any, List, Optional
from app.config import logger
from app.database.repositories.shared_repositories.audience_room_repository import find_audience_room_by_id
from app.services.comment_context_summary_service.clients.storage import get_storage_client
from app.services.comment_context_summary_service.config import CommentContextSummaryConfig
from app.services.comment_context_summary_service.orchestrators.comment_processor import CommentProcessor
from app.services.comment_context_summary_service.utils import get_comment_context_meta_key, get_comment_context_run_key
from app.services.comment_context_summary_service.job_status_manager import JobStatusManager
from app.services.comment_context_summary_service.orchestrators.chunk_orchestrator import ChunkOrchestrator
from app.utils.s3_utils import get_s3_key_for_audience


class ChunkProcessor:
    """Processes chunks of profiles for comment context summary."""

    def __init__(self, comment_processor: CommentProcessor, storage_client, config: CommentContextSummaryConfig):
        """Initialize chunk processor."""
        self.comment_processor = comment_processor
        self.storage_client = storage_client
        self.config = config

    async def process_chunk(
        self,
        job_id: str,
        audience_room_id: str,
        enterprise_name: Optional[str],
        start_profile_index: int,
        chunk_size: int,
        base_url: str,
    ) -> None:
        """
        Process one chunk of profiles.
        
        Args:
            job_id: Job ID
            audience_room_id: Audience room ID
            enterprise_name: Enterprise name for database routing
            start_profile_index: Starting profile index
            chunk_size: Number of profiles per chunk
            base_url: Base URL for triggering next chunk
        """
        # Coerce to int (query params / event payloads may arrive as str)
        start_profile_index = int(start_profile_index)
        chunk_size = int(chunk_size)
        enterprise_name = enterprise_name or "default"

        try:
            logger.info(f"[CommentContextSummaryJob {job_id}] Starting chunk processing at index {start_profile_index}")
            
            # Mark job as processing if this is the first chunk
            if start_profile_index == 0:
                logger.info(f"[CommentContextSummaryJob {job_id}] Marking job as processing")
                JobStatusManager.mark_job_processing(job_id, enterprise_name)
                logger.info(f"[CommentContextSummaryJob {job_id}] Job marked as processing")
            
            # Load audience room
            logger.info(f"[CommentContextSummaryJob {job_id}] Loading audience room {audience_room_id}")
            room = await asyncio.to_thread(find_audience_room_by_id, 
                audience_room_id,
                include_profiles=True,
                enterprise_name=enterprise_name,
            )
            logger.info(f"[CommentContextSummaryJob {job_id}] Audience room loaded: {room is not None}")
            if not room or not room.profiles:
                logger.error(f"[CommentContextSummaryJob {job_id}] Audience room not found or has no profiles")
                JobStatusManager.fail_job(
                    job_id,
                    error=f"Audience room {audience_room_id} not found or has no profiles",
                    enterprise_name=enterprise_name
                )
                return
            
            logger.info(f"[CommentContextSummaryJob {job_id}] Found {len(room.profiles)} profiles")
            
            profiles = room.profiles
            total_profiles = len(profiles)
            
            # Check if job should be completed
            if JobStatusManager.complete_job_if_no_more_profiles(
                job_id, start_profile_index, total_profiles, enterprise_name
            ):
                return

            # Load job meta to get run_ids and source
            meta_key = get_comment_context_meta_key(enterprise_name, audience_room_id)
            try:
                meta = self.storage_client.read_json(meta_key)
            except Exception as e:
                logger.error(f"[Job {job_id}] Job meta not found: {meta_key} - {e}")
                JobStatusManager.fail_job(job_id, f"Job meta not found: {e}", enterprise_name)
                return

            if meta.get("status") != "scraping_complete":
                JobStatusManager.fail_job(
                    job_id,
                    "Job scraping not complete. Poll comment-context status first.",
                    enterprise_name,
                )
                return

            source = meta.get("source") or "reddit"
            run_ids = meta.get("run_ids") or []

            # Load post_url_to_items
            post_url_to_items = await self._load_post_url_to_items(
                audience_room_id, enterprise_name, run_ids
            )

            # Prepare chunk
            profiles_chunk = profiles[start_profile_index : start_profile_index + chunk_size]
            
            logger.info(
                f"[CommentContextSummaryJob {job_id}] Chunk starting: profiles {start_profile_index}–"
                f"{min(start_profile_index + chunk_size, total_profiles) - 1} (of {total_profiles} total)"
            )

            # Load comments from chunk profiles
            all_comments_chunk = []
            profile_comments_map = {}  # profile_id -> comments list
            comment_id_to_profile_map = {}  # comment_id -> profile_id (for matching results back)

            for profile in profiles_chunk:
                comments_key = get_s3_key_for_audience(
                    audience_room_id,
                    f"profiles/{profile.id}/comments.json",
                    enterprise_name,
                    source,
                )
                try:
                    comments = await asyncio.to_thread(
                        self.storage_client.read_json, comments_key
                    )
                    if isinstance(comments, list):
                        # Limit comments per profile
                        comments_to_process = comments[: self.config.comments_per_profile]
                        # Track comment IDs for matching
                        for comment in comments_to_process:
                            comment_id = comment.get("id")
                            if comment_id:
                                comment_id_to_profile_map[comment_id] = profile.id
                        all_comments_chunk.extend(comments_to_process)
                        profile_comments_map[profile.id] = comments
                except Exception as e:
                    logger.warning(f"[Profile {profile.id}] Failed to load comments: {e}")
                    profile_comments_map[profile.id] = []

            if not all_comments_chunk:
                await self._handle_empty_chunk(
                    job_id, start_profile_index, chunk_size, total_profiles,
                    base_url, audience_room_id, enterprise_name
                )
                return

            # Calculate this chunk's total comments and add to running total
            chunk_total_comments = 0
            for profile in profiles_chunk:
                try:
                    comments_key = get_s3_key_for_audience(
                        audience_room_id,
                        f"profiles/{profile.id}/comments.json",
                        enterprise_name,
                        source,
                    )
                    comments = await asyncio.to_thread(
                        self.storage_client.read_json, comments_key
                    )
                    if isinstance(comments, list):
                        chunk_total_comments += min(len(comments), self.config.comments_per_profile)
                except Exception as e:
                    logger.warning(f"[Profile {profile.id}] Failed to count comments: {e}")
            
            # Update running total
            JobStatusManager.add_to_total_comments(job_id, chunk_total_comments, enterprise_name)
            
            logger.info(
                f"[CommentContextSummaryJob {job_id}] Chunk has {chunk_total_comments} total comments, {len(all_comments_chunk)} to process"
            )
            
            # Add progress tracking start
            logger.info(f"[CommentContextSummaryJob {job_id}] Starting AI processing of {len(all_comments_chunk)} comments...")

            # Process all comments using CommentProcessor
            logger.info(f"[CommentContextSummaryJob {job_id}] Calling AI processor...")
            results = await self.comment_processor.process_comments(
                all_comments_chunk, post_url_to_items, self.config
            )
            logger.info(f"[CommentContextSummaryJob {job_id}] AI processing completed, got {len(results)} results")

            # Group results back by profile and update S3
            enriched_count = 0
            skipped_count = 0

            # Match results back to comments by ID (more robust than index)
            comment_id_to_result = {}
            for result in results:
                comment = result.get("comment", {})
                comment_id = comment.get("id")
                if comment_id:
                    comment_id_to_result[comment_id] = result

            # Update comments by profile
            for profile in profiles_chunk:
                profile_id = profile.id
                comments = profile_comments_map.get(profile_id, [])
                if not comments:
                    continue

                # Update comments with results (match by ID)
                comments_to_update = comments[: self.config.comments_per_profile]
                for i, comment in enumerate(comments_to_update):
                    comment_id = comment.get("id")
                    if comment_id and comment_id in comment_id_to_result:
                        result = comment_id_to_result[comment_id]
                        if result.get("status") == "success":
                            # Update comment with context and summary
                            updated_comment = result.get("comment", comment)
                            comments[i] = updated_comment
                            enriched_count += 1
                        else:
                            skipped_count += 1

                # Save updated comments to S3
                comments_key = get_s3_key_for_audience(
                    audience_room_id,
                    f"profiles/{profile_id}/comments.json",
                    enterprise_name,
                    source,
                )
                try:
                    await asyncio.to_thread(
                        self.storage_client.write_json, comments_key, comments
                    )
                except Exception as e:
                    logger.error(f"[Profile {profile_id}] Failed to save comments: {e}")

            logger.info(
                f"[CommentContextSummaryJob {job_id}] Chunk complete: {enriched_count} enriched, "
                f"{skipped_count} skipped"
            )

            # Update job progress
            JobStatusManager.update_progress(
                job_id=job_id,
                processed_profiles=len(profiles_chunk),
                enriched_comments=enriched_count,
                skipped_comments=skipped_count,
                failed_comments=0,
                enterprise_name=enterprise_name,
            )

            # Handle completion - trigger next chunk or complete job
            await ChunkOrchestrator.handle_chunk_completion(
                base_url, job_id, audience_room_id, enterprise_name,
                start_profile_index, chunk_size, total_profiles
            )

        except Exception as e:
            logger.error(f"[CommentContextSummaryJob {job_id}] Chunk failed: {e}", exc_info=True)
            JobStatusManager.fail_job(job_id, error=str(e), enterprise_name=enterprise_name)

    async def _handle_empty_chunk(
        self, job_id: str, start_profile_index: int, chunk_size: int,
        total_profiles: int, base_url: str, audience_room_id: str, enterprise_name: Optional[str]
    ) -> None:
        """Handle empty chunk."""
        logger.info(f"[CommentContextSummaryJob {job_id}] Chunk has no comments to process")
        job = JobStatusManager.get_job(job_id, enterprise_name)
        next_start = start_profile_index + chunk_size
        
        if next_start < total_profiles:
            await ChunkOrchestrator.trigger_next_chunk(
                base_url, job_id, audience_room_id, enterprise_name, next_start, chunk_size
            )
        else:
            JobStatusManager.complete_job(
                job_id=job_id, result=job.result or {},
                enriched_comments=job.enriched_comments or 0,
                skipped_comments=job.skipped_comments or 0,
                failed_comments=job.failed_comments or 0,
                total_profiles=job.total_profiles or 0,
                processed_profiles=job.processed_profiles or 0,
                total_comments=job.total_comments or 0,
                enterprise_name=enterprise_name
            )

    async def _load_post_url_to_items(
        self, audience_room_id: str, enterprise_name: str, run_ids: List[str]
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Load all run result files from S3 in parallel and build post_url -> items map."""
        
        async def load_single_run(run_id: str):
            """Load a single run file."""
            key = get_comment_context_run_key(enterprise_name, audience_room_id, run_id)
            try:
                data = await asyncio.to_thread(self.storage_client.read_json, key)
                return data.get("items", []) if isinstance(data, dict) else []
            except Exception as e:
                logger.warning(f"[CommentContextSummary] Failed to load run {run_id}: {e}")
                return []
        
        run_data_list = await asyncio.gather(*[load_single_run(run_id) for run_id in run_ids])
        
        post_url_to_items: Dict[str, List[Dict[str, Any]]] = {}
        for items in run_data_list:
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "post":
                    url = item.get("url")
                    if url:
                        url = url.rstrip("/")
                        if url not in post_url_to_items:
                            post_url_to_items[url] = []
                        post_url_to_items[url].append(item)
        
        return post_url_to_items
