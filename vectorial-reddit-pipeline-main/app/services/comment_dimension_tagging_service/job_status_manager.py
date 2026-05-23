"""Job status management for comment dimension tagging."""
from typing import Dict, Any, Optional
from app.config import logger
from .repositories.comment_dimension_tagging_job_repository import (
    update_job_status,
    mark_job_completed,
    mark_job_failed,
    find_comment_dimension_tagging_job_by_id,
    increment_completed_chunks,
)


class JobStatusManager:
    """Manages job status updates and completion for comment dimension tagging."""
    
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
    def update_total_comments(job_id: str, total_comments: int, enterprise_name: Optional[str] = None) -> None:
        """Update total comments count."""
        update_job_status(
            job_id=job_id,
            total_comments=total_comments,
            enterprise_name=enterprise_name,
        )
    
    @staticmethod
    def update_progress(
        job_id: str,
        processed_comments: int,
        tagged_comments: int,
        failed_comments: int,
        skipped_comments: int,
        enterprise_name: Optional[str] = None
    ) -> None:
        """Update job progress."""
        update_job_status(
            job_id=job_id,
            processed_comments=processed_comments,
            tagged_comments=tagged_comments,
            failed_comments=failed_comments,
            skipped_comments=skipped_comments,
            enterprise_name=enterprise_name,
        )
    
    @staticmethod
    def complete_job(
        job_id: str,
        result: Dict[str, Any],
        tagged_comments: int,
        failed_comments: int,
        skipped_comments: int,
        total_comments: int,
        processed_comments: int,
        enterprise_name: Optional[str] = None
    ) -> None:
        """Mark job as completed."""
        mark_job_completed(
            job_id=job_id,
            result=result,
            tagged_comments=tagged_comments,
            failed_comments=failed_comments,
            skipped_comments=skipped_comments,
            total_comments=total_comments,
            processed_comments=processed_comments,
            enterprise_name=enterprise_name,
        )
    
    @staticmethod
    def fail_job(job_id: str, error: str, enterprise_name: Optional[str] = None) -> None:
        """Mark job as failed."""
        mark_job_failed(job_id=job_id, error=error, enterprise_name=enterprise_name)
    
    @staticmethod
    def get_job(job_id: str, enterprise_name: Optional[str] = None):
        """Get job by ID."""
        return find_comment_dimension_tagging_job_by_id(job_id, enterprise_name=enterprise_name)
    
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
                    tagged_comments=job.tagged_comments or 0,
                    failed_comments=job.failed_comments or 0,
                    skipped_comments=job.skipped_comments or 0,
                    total_comments=job.total_comments or 0,
                    processed_comments=job.processed_comments or 0,
                    enterprise_name=enterprise_name,
                )
                return True
        return False
    
    @staticmethod
    def check_and_complete_job_if_all_chunks_done(
        job_id: str,
        enterprise_name: Optional[str] = None
    ) -> bool:
        """
        Check if all chunks are completed and mark job as done.
        
        Returns:
            True if job was completed, False otherwise
        """
        job = JobStatusManager.get_job(job_id, enterprise_name)
        if not job:
            return False
        
        if job.status != "PROCESSING":
            return False
        
        # Check if all chunks are done
        if job.completed_chunks >= job.total_chunks and job.total_chunks > 0:
            JobStatusManager.complete_job(
                job_id=job_id,
                result=job.result or {},
                tagged_comments=job.tagged_comments or 0,
                failed_comments=job.failed_comments or 0,
                skipped_comments=job.skipped_comments or 0,
                total_comments=job.total_comments or 0,
                processed_comments=job.processed_comments or 0,
                enterprise_name=enterprise_name,
            )
            return True
        
        return False
    
    @staticmethod
    def increment_chunk_completion(
        job_id: str,
        enterprise_name: Optional[str] = None
    ) -> int:
        """
        Atomically increment completed chunks counter.
        
        Returns:
            New completed_chunks count
        """
        return increment_completed_chunks(job_id, enterprise_name)
