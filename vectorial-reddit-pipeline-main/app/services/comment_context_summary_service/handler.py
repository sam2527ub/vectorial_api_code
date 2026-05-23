"""Handler for comment context summary: load S3 scraped data, add context + AI summary via gateway."""
import asyncio
from typing import Dict, Any, List, Optional
from fastapi import HTTPException
from app.config import logger
from app.database.repositories.shared_repositories.audience_room_repository import find_audience_room_by_id
from app.services.comment_context_summary_service.clients.storage import get_storage_client
from app.services.comment_context_summary_service.config import CommentContextSummaryConfig
from app.services.comment_context_summary_service.orchestrators.profile_enricher import ProfileEnricher
from app.services.comment_context_summary_service.orchestrators.comment_processor import CommentProcessor
from app.services.comment_context_summary_service.orchestrators.chunk_processor import ChunkProcessor
from app.services.comment_context_summary_service.utils import (
    get_comment_context_meta_key,
    get_comment_context_run_key,
)
from app.services.ai_gateway_service import ai_gateway


class CommentContextSummaryHandler:
    """Loads scraped data from S3, adds context and AI summary to each profile's comments.json."""

    def __init__(self):
        self.config = CommentContextSummaryConfig()
        self.storage_client = get_storage_client()
        self.comment_processor = CommentProcessor()  # Shared processor for rate limiting
        self.profile_enricher = ProfileEnricher(self.config, self.comment_processor)
        self.chunk_processor = ChunkProcessor(
            self.comment_processor, self.storage_client, self.config
        )

    def _validate(self) -> None:
        if not self.storage_client.is_configured():
            raise HTTPException(status_code=503, detail="Storage client not configured.")
        if not self.profile_enricher.ai_client.is_configured():
            raise HTTPException(
                status_code=503,
                detail="AI Gateway not configured. Set AI_GATEWAY_API_KEY or configure gateway.",
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

    async def process_job_chunk(
        self,
        job_id: str,
        audience_room_id: str,
        enterprise_name: Optional[str],
        start_profile_index: int,
        base_url: str,
        chunk_size: int,
    ) -> None:
        """Process one chunk of profiles."""
        logger.info(f"[CommentContextSummary] process_job_chunk called: job_id={job_id}, start_index={start_profile_index}")
        await self.chunk_processor.process_chunk(
            job_id, audience_room_id, enterprise_name, start_profile_index, chunk_size, base_url
        )
        logger.info(f"[CommentContextSummary] process_job_chunk completed for job_id={job_id}")

    async def handle_request(
        self, audience_room_id: str, enterprise_name: str
    ) -> Dict[str, Any]:
        """Load job meta and run data from S3; add context + summary to all profiles' comments.json."""
        self._validate()
        enterprise_name = enterprise_name or "default"

        # Read job meta directly by audience_room_id
        meta_key = get_comment_context_meta_key(enterprise_name, audience_room_id)
        try:
            meta = self.storage_client.read_json(meta_key)
        except Exception as e:
            logger.warning(f"[CommentContextSummary] Job meta not found: {meta_key} - {e}")
            raise HTTPException(
                status_code=404,
                detail=f"No scraping job found for audience room {audience_room_id}. Run comment-context/start first.",
            )

        if meta.get("status") != "scraping_complete":
            raise HTTPException(
                status_code=400,
                detail="Job scraping not complete. Poll comment-context status first.",
            )

        source = meta.get("source") or "reddit"
        run_ids = meta.get("run_ids") or []

        room = find_audience_room_by_id(
            audience_room_id,
            include_profiles=True,
            enterprise_name=enterprise_name,
        )
        if not room or not room.profiles:
            raise HTTPException(status_code=404, detail="Room or profiles not found.")
        profiles = room.profiles

        job_id = meta.get("job_id")
        logger.info(
            f"[CommentContextSummary] Loading scraped data for room {audience_room_id} (job {job_id}), "
            f"{len(run_ids)} runs, {len(profiles)} profiles"
        )
        post_url_to_items = await self._load_post_url_to_items(
            audience_room_id, enterprise_name, run_ids
        )
        logger.info(
            f"[CommentContextSummary] Built map for {len(post_url_to_items)} post URLs"
        )

        successful_profiles = 0
        failed_profiles = 0
        total_enriched = 0
        total_skipped = 0
        errors: List[Dict[str, Any]] = []

        # Use chunk size for backward compatibility (old handle_request method)
        max_concurrent = getattr(self.config, "max_concurrent_profiles", self.config.chunk_size_profiles)
        semaphore = asyncio.Semaphore(max_concurrent)

        async def enrich_profile_with_semaphore(profile):
            """Enrich a single profile with semaphore control."""
            async with semaphore:
                return await self.profile_enricher.enrich_profile_comments(
                    profile.id,
                    audience_room_id,
                    enterprise_name,
                    source,
                    post_url_to_items,
                )

        tasks = [enrich_profile_with_semaphore(profile) for profile in profiles]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                failed_profiles += 1
                errors.append({"error": str(result)})
            elif result.get("status") == "success":
                successful_profiles += 1
                total_enriched += result.get("comments_enriched", 0)
                total_skipped += result.get("comments_skipped", 0)
            else:
                failed_profiles += 1
                errors.append(result)

        overall = (
            "succeeded"
            if failed_profiles == 0
            else "partial" if successful_profiles > 0 else "failed"
        )
        return {
            "status": overall,
            "job_id": job_id,
            "audience_room_id": audience_room_id,
            "total_profiles": len(profiles),
            "successful_profiles": successful_profiles,
            "failed_profiles": failed_profiles,
            "total_comments_enriched": total_enriched,
            "total_comments_skipped": total_skipped,
            "errors": errors,
            "message": (
                f"Enriched {total_enriched} comments across {successful_profiles} profiles. "
                f"Skipped {total_skipped} comments."
            ),
        }
