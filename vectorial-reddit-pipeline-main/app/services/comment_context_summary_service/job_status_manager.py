"""Job status management for comment context summary."""
from typing import Dict, Any, Optional
from app.config import logger
from .repositories.comment_context_summary_job_repository import (
    update_job_status,
    mark_job_completed,
    mark_job_failed,
    find_comment_context_summary_job_by_id,
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
    def update_total_profiles(job_id: str, total_profiles: int, enterprise_name: Optional[str] = None) -> None:
        """Update total profiles count."""
        update_job_status(
            job_id=job_id,
            total_profiles=total_profiles,
            enterprise_name=enterprise_name,
        )
    
    @staticmethod
    def add_to_total_comments(job_id: str, chunk_comments: int, enterprise_name: Optional[str] = None) -> None:
        """Add chunk comments to total comments count."""
        job = JobStatusManager.get_job(job_id, enterprise_name)
        if job:
            new_total = (job.total_comments or 0) + chunk_comments
            update_job_status(
                job_id=job_id,
                total_comments=new_total,
                enterprise_name=enterprise_name,
            )
    
    @staticmethod
    def update_progress(
        job_id: str,
        processed_profiles: int,
        enriched_comments: int,
        skipped_comments: int,
        failed_comments: int,
        enterprise_name: Optional[str] = None
    ) -> None:
        """Update job progress."""
        job = JobStatusManager.get_job(job_id, enterprise_name)
        if job:
            update_job_status(
                job_id=job_id,
                processed_profiles=(job.processed_profiles or 0) + processed_profiles,
                enriched_comments=(job.enriched_comments or 0) + enriched_comments,
                skipped_comments=(job.skipped_comments or 0) + skipped_comments,
                failed_comments=(job.failed_comments or 0) + failed_comments,
                enterprise_name=enterprise_name,
            )
    
    @staticmethod
    def complete_job(
        job_id: str,
        result: Dict[str, Any],
        enriched_comments: int,
        skipped_comments: int,
        failed_comments: int,
        total_profiles: Optional[int] = None,
        processed_profiles: Optional[int] = None,
        total_comments: Optional[int] = None,
        enterprise_name: Optional[str] = None
    ) -> None:
        """Mark job as completed."""
        mark_job_completed(
            job_id=job_id,
            result=result,
            enriched_comments=enriched_comments,
            skipped_comments=skipped_comments,
            failed_comments=failed_comments,
            total_profiles=total_profiles,
            processed_profiles=processed_profiles,
            total_comments=total_comments,
            enterprise_name=enterprise_name,
        )
    
    @staticmethod
    def fail_job(job_id: str, error: str, enterprise_name: Optional[str] = None) -> None:
        """Mark job as failed."""
        mark_job_failed(job_id=job_id, error=error, enterprise_name=enterprise_name)
    
    @staticmethod
    def get_job(job_id: str, enterprise_name: Optional[str] = None):
        """Get job by ID."""
        return find_comment_context_summary_job_by_id(job_id, enterprise_name=enterprise_name)
    
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
                    enriched_comments=job.enriched_comments or 0,
                    skipped_comments=job.skipped_comments or 0,
                    failed_comments=job.failed_comments or 0,
                    total_profiles=job.total_profiles or 0,
                    processed_profiles=job.processed_profiles or 0,
                    total_comments=job.total_comments or 0,
                    enterprise_name=enterprise_name,
                )
                return True
        return False
