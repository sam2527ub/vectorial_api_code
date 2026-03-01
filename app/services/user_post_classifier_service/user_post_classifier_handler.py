"""Handler for User Post Classifier: trigger async jobs, delegate processing, status."""
import uuid
from typing import Any, Dict, Optional

from fastapi import HTTPException

from app.config import logger
from app.utils.helpers import ensure_db_available
from app import database

from .config import UserPostClassifierConfig
from .repositories import (
    create_classifier_job,
    get_classifier_job,
)
from .job_processor import process_classifier_job as run_job


class UserPostClassifierHandler:
    """Trigger async classifier jobs, process via job_processor, expose status."""

    def __init__(self, config: Optional[UserPostClassifierConfig] = None):
        self.config = config or UserPostClassifierConfig()
        self.batch_size = self.config.batch_size

    def _validate(self) -> None:
        ensure_db_available("audience")
        from app.config import s3_client, s3_bucket
        if not s3_client or not s3_bucket:
            raise HTTPException(status_code=503, detail="S3 is not configured.")

    def trigger_async_classifier(
        self,
        audience_room_id: str,
        classifier_id: str,
        enterprise_name: Optional[str] = None,
        batch_size: Optional[int] = None,
        task_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create job record and return. Caller must run process_classifier_job in background."""
        self._validate()
        classifier = database.find_post_classifier_by_id(classifier_id)
        if not classifier:
            raise HTTPException(status_code=404, detail=f"Classifier {classifier_id} not found")

        size = batch_size if batch_size is not None else self.batch_size
        job_id = str(uuid.uuid4())
        create_classifier_job(
            job_id=job_id,
            classifier_id=classifier_id,
            audience_room_id=audience_room_id,
            task_token=task_token,
            enterprise_name=enterprise_name,
        )
        return {
            "job_id": job_id,
            "status": "PENDING",
            "classifier_id": classifier_id,
            "audience_room_id": audience_room_id,
            "message": "Classifier job started. Poll /api/classifier/async/status/{job_id} for progress.",
        }

    async def process_classifier_job(
        self,
        job_id: str,
        audience_room_id: str,
        classifier_id: str,
        enterprise_name: Optional[str],
        batch_size: int,
    ) -> None:
        """Background task: run full job via job_processor."""
        await run_job(
            job_id=job_id,
            audience_room_id=audience_room_id,
            classifier_id=classifier_id,
            enterprise_name=enterprise_name,
            batch_size=batch_size,
            config=self.config,
        )

    def get_job_status(self, job_id: str, enterprise_name: Optional[str] = None) -> Dict[str, Any]:
        """Return job status dict. Raises 404 if not found."""
        job = get_classifier_job(job_id, enterprise_name)
        if not job:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
        return {
            "job_id": job["job_id"],
            "status": job["status"],
            "classifier_id": job["classifier_id"],
            "audience_room_id": job["audience_room_id"],
            "total_profiles": job["total_profiles"],
            "processed_profiles": job["processed_profiles"],
            "total_posts_classified": job["total_posts_classified"],
            "error": job["error"],
            "created_at": job["created_at"],
            "updated_at": job["updated_at"],
        }
