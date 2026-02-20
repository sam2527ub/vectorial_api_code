"""Upload audience room description JSON to S3."""
from typing import Optional

from app.utils.s3_utils import get_s3_key_for_audience, upload_json_to_s3


def upload_audience_description(
    room_id: str,
    audience_room_name: str,
    audience_description: str,
    enterprise_name: Optional[str],
    source: Optional[str],
) -> str:
    """Upload description.json to S3; return the S3 URL."""
    key = get_s3_key_for_audience(room_id, "description.json", enterprise_name, source)
    payload = {
        "audience_room_id": room_id,
        "audience_room_name": audience_room_name,
        "description": audience_description,
    }
    return upload_json_to_s3(key, payload)
