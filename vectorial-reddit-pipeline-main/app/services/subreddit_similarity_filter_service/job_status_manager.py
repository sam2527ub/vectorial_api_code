"""Job status management for subreddit similarity filter."""
from typing import Dict, Any, Optional
from app.config import logger
from .repositories.subreddit_similarity_filter_job_repository import (
    update_job_status,
    mark_job_completed,
    mark_job_failed,
    find_subreddit_similarity_filter_job_by_id,
)


class JobStatusManager:
    """Manages job status updates and completion."""

    @staticmethod
    def mark_processing(job_id: str, enterprise_name: Optional[str] = None) -> None:
        update_job_status(
            job_id=job_id, status="PROCESSING", started=True,
            enterprise_name=enterprise_name,
        )

    @staticmethod
    def update_totals(
        job_id: str,
        total_subreddits: int,
        total_users_before: int,
        enterprise_name: Optional[str] = None,
    ) -> None:
        update_job_status(
            job_id=job_id,
            total_subreddits=total_subreddits,
            total_users_before=total_users_before,
            enterprise_name=enterprise_name,
        )

    @staticmethod
    def update_progress(
        job_id: str,
        checked_subreddits: int,
        enterprise_name: Optional[str] = None,
    ) -> None:
        update_job_status(
            job_id=job_id,
            checked_subreddits=checked_subreddits,
            enterprise_name=enterprise_name,
        )

    @staticmethod
    def complete_job(
        job_id: str,
        result: Dict[str, Any],
        total_users_before: int,
        total_users_after: int,
        total_subreddits: int,
        checked_subreddits: int,
        result_s3_key: Optional[str] = None,
        enterprise_name: Optional[str] = None,
    ) -> None:
        mark_job_completed(
            job_id=job_id, result=result,
            total_users_before=total_users_before,
            total_users_after=total_users_after,
            total_subreddits=total_subreddits,
            checked_subreddits=checked_subreddits,
            result_s3_key=result_s3_key,
            enterprise_name=enterprise_name,
        )

    @staticmethod
    def fail_job(job_id: str, error: str, enterprise_name: Optional[str] = None) -> None:
        mark_job_failed(job_id=job_id, error=error, enterprise_name=enterprise_name)

    @staticmethod
    def get_job(job_id: str, enterprise_name: Optional[str] = None):
        return find_subreddit_similarity_filter_job_by_id(job_id, enterprise_name=enterprise_name)
