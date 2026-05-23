"""Shared database repositories used by multiple services."""
from app.database.repositories.shared_repositories.audience_profile_repository import (
    find_audience_profiles,
    find_audience_profile_by_id,
    update_audience_profile,
    delete_audience_profiles_by_room
)
from app.database.repositories.shared_repositories.audience_room_repository import (
    find_audience_room_by_id,
    update_audience_room
)
from app.database.repositories.shared_repositories.audience_room_preview_repository import (
    ensure_audience_room_preview_columns_exist,
    upsert_audience_room_with_profiles,
    set_audience_room_status,
)
from app.database.repositories.shared_repositories.reddit_workflow_job_repository import (
    ensure_reddit_workflow_job_table_exists,
    create_reddit_workflow_job,
    find_reddit_workflow_job_by_job_id,
    find_reddit_workflow_job_by_run_id,
    update_reddit_workflow_job_progress,
    update_reddit_workflow_job_run_id
)

__all__ = [
    "find_audience_profiles",
    "find_audience_profile_by_id",
    "update_audience_profile",
    "delete_audience_profiles_by_room",
    "find_audience_room_by_id",
    "update_audience_room",
    "ensure_audience_room_preview_columns_exist",
    "upsert_audience_room_with_profiles",
    "set_audience_room_status",
    "ensure_reddit_workflow_job_table_exists",
    "create_reddit_workflow_job",
    "find_reddit_workflow_job_by_job_id",
    "find_reddit_workflow_job_by_run_id",
    "update_reddit_workflow_job_progress",
    "update_reddit_workflow_job_run_id"
]
