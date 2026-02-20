"""Upload profile payloads to S3 and build profile records for database insertion."""
import uuid
from typing import Any, Dict, List, Optional

from app.config import logger
from app.utils.s3_utils import get_s3_key_for_audience, upload_json_to_s3


def upload_profile_payloads_and_build_records(
    room_id: str,
    profiles: List[Any],
    enterprise_name: Optional[str],
    source: Optional[str],
) -> List[Dict[str, Any]]:
    """
    For each profile, upload profile.json to S3 and build the record dict for create_audience_room.
    Returns list of dicts with id, profileName, profileUrl, profileDescriptionS3Url, postsS3Url.
    """
    profile_creates = []
    for idx, profile in enumerate(profiles):
        profile_id = str(uuid.uuid4())
        profile_key = get_s3_key_for_audience(
            room_id, f"profiles/{profile_id}/profile.json", enterprise_name, source
        )
        profile_payload = {
            "profile_id": profile_id,
            "audience_room_id": room_id,
            "name": profile.name,
            "age": profile.age,
            "current_company": profile.current_company,
            "current_location": profile.current_location,
            "total_years_experience": profile.total_years_experience,
            "industry": profile.industry,
            "education": profile.education,
            "linkedin_profile_url": profile.linkedin_profile_url,
            "jobTitle": getattr(profile, "jobTitle", None),
            "headline": getattr(profile, "headline", None),
            "about": getattr(profile, "about", None),
            "summary": None,
        }
        profile_url = upload_json_to_s3(profile_key, profile_payload)
        profile_creates.append({
            "id": profile_id,
            "profileName": profile.name,
            "profileUrl": profile.linkedin_profile_url,
            "profileDescriptionS3Url": profile_url,
            "postsS3Url": None,
        })
        logger.debug(f"Uploaded profile {idx + 1}/{len(profiles)}: profile_id={profile_id}")
    return profile_creates
