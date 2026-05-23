"""Job status management for dimension tagging."""
from typing import Dict, Any, Optional
from app.config import logger
from .repositories.post_dimension_tagging_job_repository import (
    update_job_status,
    mark_job_completed,
    mark_job_failed,
    find_post_dimension_tagging_job_by_id,
)


class JobStatusManager:
    """Manages job status updates and completion."""
    
    @staticmethod
    def mark_job_processing(job_id: str, enterprise_name: Optional[str] = None) -> None:
        """Mark job as processing."""
        update_job_status(
            job_id=job_id,
            status="PROCESSING",
            started=True,
            enterprise_name=enterprise_name,
        )
    
    @staticmethod
    def update_total_posts(job_id: str, total_posts: int, enterprise_name: Optional[str] = None) -> None:
        """Update total posts count."""
        update_job_status(
            job_id=job_id,
            total_posts=total_posts,
            enterprise_name=enterprise_name,
        )
    
    @staticmethod
    def update_progress(
        job_id: str,
        processed_posts: int,
        tagged_posts: int,
        failed_posts: int,
        enterprise_name: Optional[str] = None
    ) -> None:
        """Update job progress."""
        update_job_status(
            job_id=job_id,
            processed_posts=processed_posts,
            tagged_posts=tagged_posts,
            failed_posts=failed_posts,
            enterprise_name=enterprise_name,
        )
    
    @staticmethod
    def complete_job(
        job_id: str,
        result: Dict[str, Any],
        tagged_posts: int,
        failed_posts: int,
        total_posts: int,
        processed_posts: int,
        enterprise_name: Optional[str] = None
    ) -> None:
        """Mark job as completed."""
        mark_job_completed(
            job_id=job_id,
            result=result,
            tagged_posts=tagged_posts,
            failed_posts=failed_posts,
            total_posts=total_posts,
            processed_posts=processed_posts,
            enterprise_name=enterprise_name,
        )
    
    @staticmethod
    def fail_job(job_id: str, error: str, enterprise_name: Optional[str] = None) -> None:
        """Mark job as failed."""
        mark_job_failed(job_id=job_id, error=error, enterprise_name=enterprise_name)
    
    @staticmethod
    def get_job(job_id: str, enterprise_name: Optional[str] = None):
        """Get job by ID."""
        return find_post_dimension_tagging_job_by_id(job_id, enterprise_name=enterprise_name)
    
    @staticmethod
    def complete_job_if_no_more_profiles(
        job_id: str,
        start_profile_index: int,
        total_profiles: int,
        enterprise_name: Optional[str] = None
    ) -> bool:
        """
        Complete job if no more profiles to process.
        
        Returns:
            True if job was completed, False otherwise
        """
        if start_profile_index >= total_profiles:
            job = JobStatusManager.get_job(job_id, enterprise_name)
            if job and job.status == "PROCESSING":
                JobStatusManager.complete_job(
                    job_id=job_id,
                    result=job.result or {},
                    tagged_posts=job.tagged_posts or 0,
                    failed_posts=job.failed_posts or 0,
                    total_posts=job.total_posts or 0,
                    processed_posts=job.processed_posts or 0,
                    enterprise_name=enterprise_name,
                )
                return True
        return False
