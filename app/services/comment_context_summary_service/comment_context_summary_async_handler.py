"""Async API handler: trigger comment context summary jobs, process chunks, and return job status."""

import uuid
from typing import Any, Dict, Optional

from fastapi import HTTPException

from app import database
from app.config import logger, s3_bucket, s3_client

from app.services.comment_context_summary_service.comment_context_summary_chunk_processor import (
    process_comment_context_summary_chunks as run_comment_context_summary_chunks,
)
from app.services.comment_context_summary_service.config import CommentContextSummaryConfig
from app.services.comment_context_summary_service.repositories.comment_context_summary_job_repository import (
    create_comment_context_summary_job,
    get_comment_context_summary_job,
)


class CommentContextSummaryAsyncHandler:
    """
    Trigger async comment context summary jobs, delegate chunk processing, and expose job status.
    """

    def __init__(self, config: Optional[CommentContextSummaryConfig] = None) -> None:
        self.config = config or CommentContextSummaryConfig()

    def _ensure_prerequisites(self) -> None:
        """Raise HTTPException if audience DB or S3 is not available."""
        if not database.is_audience_db_available():
            raise HTTPException(status_code=503, detail="Audience database not available")
        if not s3_client or not s3_bucket:
            raise HTTPException(status_code=503, detail="S3 is not configured.")

    def trigger_async_comment_context_summary(
        self,
        audience_room_id: str,
        enterprise_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a comment context summary job and return response with job_id and status."""
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

    async def process_comment_context_summary_chunks(
        self,
        job_id: str,
        audience_room_id: str,
        enterprise_name: Optional[str],
        chunk_size_profiles: int,
        start_chunk: int = 0,
        base_url: Optional[str] = None,
    ) -> None:
        """Process chunks of profiles for comment context summary and trigger next batch if needed."""
        await run_comment_context_summary_chunks(
            job_id=job_id,
            audience_room_id=audience_room_id,
            enterprise_name=enterprise_name,
            chunk_size_profiles=chunk_size_profiles,
            config=self.config,
            start_chunk=start_chunk,
            base_url=base_url,
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
