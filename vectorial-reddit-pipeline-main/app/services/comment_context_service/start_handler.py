"""Start handler for comment context scraping jobs."""
import uuid
from datetime import datetime
from typing import Dict, Any, Set, List
from fastapi import HTTPException
from app.config import logger
from app.utils.s3_utils import get_s3_key_for_audience
from app.database.repositories.shared_repositories.audience_room_repository import find_audience_room_by_id
from app.services.comment_context_service.clients.storage import get_storage_client
from app.services.comment_context_service.clients.scraping import get_scraping_client
from app.services.comment_context_service.utils.post_url_utils import (
    extract_unique_post_urls,
    batch_post_urls,
)
from app.services.comment_context_service.config import CommentContextServiceConfig
from app.services.comment_context_service.utils import (
    get_comment_context_meta_key,
)


class CommentContextStartHandler:
    """Handles starting comment context scraping jobs."""

    def __init__(self):
        self.config = CommentContextServiceConfig()
        self.storage_client = get_storage_client()
        self.scraping_client = get_scraping_client(self.config)

    def _validate(self) -> None:
        if not self.storage_client.is_configured():
            raise HTTPException(status_code=503, detail="Storage client not configured.")
        if not self.scraping_client.is_configured():
            raise HTTPException(
                status_code=503,
                detail="Scraping client not configured. Set APIFY_API_TOKEN.",
            )

    def _get_comments_s3_key(
        self, room_id: str, profile_id: str, enterprise_name: str, source: str
    ) -> str:
        return get_s3_key_for_audience(
            room_id,
            f"profiles/{profile_id}/comments.json",
            enterprise_name,
            source,
        )

    def _collect_unique_post_urls(
        self,
        profiles: list,
        audience_room_id: str,
        enterprise_name: str,
        source: str,
    ) -> List[str]:
        all_urls: Set[str] = set()
        for profile in profiles:
            try:
                key = self._get_comments_s3_key(
                    audience_room_id, profile.id, enterprise_name, source
                )
                data = self.storage_client.read_json(key)
                if isinstance(data, list):
                    comments = data[: self.config.comments_per_profile]
                    all_urls.update(extract_unique_post_urls(comments))
            except Exception as e:
                logger.warning(f"[CommentContext] Profile {profile.id} load failed: {e}")
        return list(all_urls)

    async def start_job(
        self, audience_room_id: str, enterprise_name: str
    ) -> Dict[str, Any]:
        """Start Apify runs for all unique post URLs; store job meta in S3."""
        self._validate()
        enterprise_name = enterprise_name or "default"

        room = find_audience_room_by_id(
            audience_room_id, include_profiles=True, enterprise_name=enterprise_name
        )
        if not room:
            raise HTTPException(status_code=404, detail="Audience room not found.")
        profiles = room.profiles or []
        if not profiles:
            raise HTTPException(status_code=404, detail="No profiles in room.")
        source = room.source or "reddit"

        unique_urls = self._collect_unique_post_urls(
            profiles, audience_room_id, enterprise_name, source
        )
        if not unique_urls:
            return {
                "job_id": None,
                "audience_room_id": audience_room_id,
                "status": "no_urls",
                "message": "No post URLs found in comments.",
                "total_profiles": len(profiles),
                "run_ids": [],
                "check_status_url": None,
            }

        batches = batch_post_urls(unique_urls, self.config.post_urls_per_batch)
        job_id = str(uuid.uuid4())
        profile_ids = [p.id for p in profiles]
        meta = {
            "job_id": job_id,
            "audience_room_id": audience_room_id,
            "enterprise_name": enterprise_name,
            "source": source,
            "run_ids": [],
            "profile_ids": profile_ids,
            "status": "starting",
            "fetched_run_ids": [],
            "total_batches": len(batches),
            "total_post_urls": len(unique_urls),
            "unique_urls": unique_urls,
            "post_urls_per_batch": self.config.post_urls_per_batch,
            "pending_batch_index": 0,
            "created_at": datetime.utcnow().isoformat(),
        }

        meta_key = get_comment_context_meta_key(enterprise_name, audience_room_id)
        self.storage_client.write_json(meta_key, meta)

        logger.info(
            f"[CommentContext] Job {job_id} created for room {audience_room_id}: "
            f"{len(batches)} batches, {len(unique_urls)} unique URLs. Poll status to start runs."
        )

        return {
            "job_id": job_id,
            "audience_room_id": audience_room_id,
            "status": "started",
            "run_ids": [],
            "total_batches": len(batches),
            "total_post_urls": len(unique_urls),
            "total_profiles": len(profiles),
            "check_status_url": f"/api/v1/audience-rooms/{audience_room_id}/comment-context/status?enterpriseName={enterprise_name}",
            "message": "Poll status to start runs and track progress until scraping_complete.",
        }
