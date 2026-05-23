"""S3 key helpers for comment context job storage."""
from typing import Optional


def get_comment_context_meta_key(enterprise_name: str, audience_room_id: str) -> str:
    """S3 key for job meta: {enterpriseName}/comment_context/{audience_room_id}/meta.json."""
    normalized = (enterprise_name or "default").lower().strip()
    return f"{normalized}/comment_context/{audience_room_id}/meta.json"


def get_comment_context_run_key(enterprise_name: str, audience_room_id: str, run_id: str) -> str:
    """S3 key for a single run result: {enterpriseName}/comment_context/{audience_room_id}/runs/{run_id}.json."""
    normalized = (enterprise_name or "default").lower().strip()
    return f"{normalized}/comment_context/{audience_room_id}/runs/{run_id}.json"


def get_comment_context_runs_prefix(enterprise_name: str, audience_room_id: str) -> str:
    """S3 prefix to list all run files for a job."""
    normalized = (enterprise_name or "default").lower().strip()
    return f"{normalized}/comment_context/{audience_room_id}/runs/"
