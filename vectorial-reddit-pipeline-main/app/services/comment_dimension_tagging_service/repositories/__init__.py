"""Repositories for comment dimension tagging service."""
from .comment_dimension_tagging_job_repository import (
    ensure_comment_dimension_tagging_job_table_exists,
    create_comment_dimension_tagging_job,
    find_comment_dimension_tagging_job_by_id,
    get_pending_jobs,
    update_job_status,
    mark_job_completed,
    mark_job_failed,
    increment_completed_chunks,
)

__all__ = [
    "ensure_comment_dimension_tagging_job_table_exists",
    "create_comment_dimension_tagging_job",
    "find_comment_dimension_tagging_job_by_id",
    "get_pending_jobs",
    "update_job_status",
    "mark_job_completed",
    "mark_job_failed",
    "increment_completed_chunks",
]
