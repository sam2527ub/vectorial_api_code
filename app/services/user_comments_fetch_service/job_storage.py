"""
Store comment-scrape job metadata in S3 under the audience room folder.
Path: {enterprise}/{audience_type}/{room_id}/linkedin-comment-context/job.json
One job file per room; starting a new run overwrites it. Poll by audience_room_id.
"""
from typing import Any, Dict, Optional

from fastapi import HTTPException

from app.utils.s3_utils import (
    get_s3_key_for_audience,
    upload_json_to_s3,
    fetch_json_from_s3,
)


JOB_FILENAME = "linkedin-comment-context/job.json"


def get_job_s3_key(
    room_id: str,
    enterprise_name: Optional[str] = None,
    source: Optional[str] = None,
) -> str:
    """S3 key for the room's comment-context job file."""
    return get_s3_key_for_audience(room_id, JOB_FILENAME, enterprise_name, source)


def save_job(key: str, job: Dict[str, Any]) -> None:
    """Write job JSON to S3 (overwrites existing)."""
    upload_json_to_s3(key, job)


def load_job(key: str) -> Optional[Dict[str, Any]]:
    """Load job JSON from S3. Returns None if key does not exist."""
    try:
        return fetch_json_from_s3(key)
    except HTTPException as e:
        if e.status_code == 404:
            return None
        raise
