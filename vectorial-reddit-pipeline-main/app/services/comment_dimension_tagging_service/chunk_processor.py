"""Chunk processing for comment dimension tagging."""
import asyncio
from typing import Dict, Any, List, Optional
from app.config import logger
from .utils.category_selector import get_category_and_dimensions
from .utils.chunk_loader import load_chunk_comments
from .utils.s3_updater import update_comments_in_s3
from .repositories.audience_room_repository import find_audience_room_with_category
from .job_status_manager import JobStatusManager
from .chunk_orchestrator import ChunkOrchestrator


class ChunkProcessor:
    """Processes chunks of profiles for comment dimension tagging."""
    
    def __init__(self, dimension_processor, storage_client, config):
        """Initialize chunk processor."""
        self.dimension_processor = dimension_processor
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
        """Process one chunk of profiles."""
        start_profile_index = int(start_profile_index)
        chunk_size = int(chunk_size)

        try:
            audience_room = await find_audience_room_with_category(
                audience_room_id,
                enterprise_name=enterprise_name,
                include_profiles=True,
            )
            if not audience_room:
                JobStatusManager.fail_job(
                    job_id,
                    error=f"Audience room {audience_room_id} not found",
                    enterprise_name=enterprise_name
                )
                return
            
            profiles = audience_room.profiles or []
            total_profiles = len(profiles)
            
            if JobStatusManager.complete_job_if_no_more_profiles(
                job_id, start_profile_index, total_profiles, enterprise_name
            ):
                return
            
            category_lower, dimensions, trait_titles, prompt_template = get_category_and_dimensions(audience_room)
            
            profiles_chunk = profiles[start_profile_index : start_profile_index + chunk_size]
            profile_ids_chunk = [p.id for p in profiles_chunk]
            
            logger.info(
                f"[CommentDimensionTaggingJob {job_id}] Chunk starting: profiles {start_profile_index}–"
                f"{min(start_profile_index + chunk_size, total_profiles) - 1} (of {total_profiles} total)"
            )
            
            all_comments_chunk, profile_comments_map_chunk = await load_chunk_comments(
                self.storage_client, job_id, profiles, profiles_chunk,
                profile_ids_chunk, start_profile_index, enterprise_name
            )
            
            if not all_comments_chunk:
                await self._handle_empty_chunk(
                    job_id, start_profile_index, chunk_size, total_profiles,
                    base_url, audience_room_id, enterprise_name
                )
                return
            
            await self._process_chunk_comments(
                job_id, all_comments_chunk, profile_comments_map_chunk,
                dimensions, trait_titles, prompt_template, enterprise_name
            )
            
            # Handle completion - trigger next chunk or complete job
            await ChunkOrchestrator.handle_chunk_completion(
                base_url, job_id, audience_room_id, enterprise_name,
                start_profile_index, chunk_size, total_profiles
            )
            
        except Exception as e:
            logger.error(f"[CommentDimensionTaggingJob {job_id}] Chunk failed: {e}", exc_info=True)
            JobStatusManager.fail_job(job_id, error=str(e), enterprise_name=enterprise_name)
    
    async def _handle_empty_chunk(
        self, job_id: str, start_profile_index: int, chunk_size: int,
        total_profiles: int, base_url: str, audience_room_id: str, enterprise_name: Optional[str]
    ) -> None:
        """Handle empty chunk."""
        job = JobStatusManager.get_job(job_id, enterprise_name)
        next_start = start_profile_index + chunk_size
        
        if next_start < total_profiles:
            await ChunkOrchestrator.trigger_next_chunk(
                base_url, job_id, audience_room_id, enterprise_name, next_start, chunk_size
            )
        else:
            JobStatusManager.complete_job(
                job_id=job_id, result=job.result or {},
                tagged_comments=job.tagged_comments or 0, failed_comments=job.failed_comments or 0,
                total_comments=job.total_comments or 0, processed_comments=job.processed_comments or 0,
                skipped_comments=job.skipped_comments or 0,
                enterprise_name=enterprise_name
            )
    
    async def _process_chunk_comments(
        self, job_id: str, all_comments_chunk: List[Dict[str, Any]],
        profile_comments_map_chunk: Dict[str, Dict[str, Any]], dimensions: List[str],
        trait_titles: List[str],
        prompt_template: Any, enterprise_name: Optional[str]
    ) -> None:
        """Process comments in chunk."""
        comments_in_chunk = len(all_comments_chunk)
        logger.info(f"[CommentDimensionTaggingJob {job_id}] Chunk has {comments_in_chunk} comments")
        
        # Tag comments (AI gateway called inside processor)
        tagging_results = await asyncio.to_thread(
            self.dimension_processor.tag_dimensions,
            all_comments_chunk,
            dimensions,
            trait_titles,
            prompt_template,
            self.config,
        )
        
        # Add dimensions to comments
        tagged = 0
        failed = 0
        for result in tagging_results:
            comment = result["comment"]
            dimensions_result = result.get("dimensions", {})
            
            # Check if processing failed (all dimensions have 0.0 confidence)
            if dimensions_result and all(
                d.get("confidence", 0.0) == 0.0 for d in dimensions_result.values()
            ):
                failed += 1
            else:
                tagged += 1
            
            # Add dimensions to comment (in-place update)
            comment["dimensions"] = dimensions_result
            comment["traits"] = result.get("traits", {})
            comment["usefulness_score"] = result.get("usefulness_score", 0.0)
        
        # Update S3
        await update_comments_in_s3(self.storage_client, profile_comments_map_chunk)
        
        # Update progress
        job = JobStatusManager.get_job(job_id, enterprise_name)
        JobStatusManager.update_progress(
            job_id,
            (job.processed_comments or 0) + len(all_comments_chunk),
            (job.tagged_comments or 0) + tagged,
            (job.failed_comments or 0) + failed,
            job.skipped_comments or 0,  # Skipped comments are tracked during loading
            enterprise_name
        )
