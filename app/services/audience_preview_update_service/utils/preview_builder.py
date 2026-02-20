"""Build preview data for audience rooms from S3 and room dict. Handles LinkedIn and Reddit schemas."""
from typing import Any, Dict, Optional

from app.config import logger
from app.utils.s3_utils import extract_s3_key_from_url, fetch_json_from_s3


def fetch_s3_json_safe(s3_url: Optional[str]) -> Dict[str, Any]:
    """Safely fetch JSON from S3, returning empty dict on failure."""
    if not s3_url:
        return {}
    try:
        key = extract_s3_key_from_url(s3_url)
        if not key:
            return {}
        return fetch_json_from_s3(key)
    except Exception as e:
        logger.warning(f"Failed to fetch S3 data from {s3_url}: {e}")
        return {}


def build_profile_preview(profile: Dict[str, Any], source: str) -> Dict[str, Any]:
    """
    Build a profile preview object by fetching data from S3.
    Handles both LinkedIn and Reddit profile schemas.
    """
    profile_data = fetch_s3_json_safe(profile.get("profileDescriptionS3Url"))
    profile_source = profile.get("source") or source or ""
    is_linkedin = profile_source.lower() == "linkedin"

    if is_linkedin:
        return {
            "id": profile.get("id"),
            "name": profile_data.get("name") or profile.get("profileName"),
            "linkedin_profile_url": profile_data.get("linkedin_profile_url") or profile.get("profileUrl"),
            "current_location": profile_data.get("current_location"),
            "current_company": profile_data.get("current_company"),
            "industry": profile_data.get("industry"),
            "summary": profile_data.get("summary"),
            "source": "linkedin",
        }
    else:
        return {
            "id": profile.get("id"),
            "name": profile_data.get("username") or profile.get("profileName"),
            "reddit_profile_url": profile_data.get("userUrl") or profile.get("profileUrl"),
            "post_count": profile_data.get("postCount"),
            "comment_count": profile_data.get("commentCount"),
            "summary": profile_data.get("summary"),
            "source": "reddit",
        }


def generate_preview_for_room(room: Dict[str, Any], preview_profile_limit: int = 5) -> Dict[str, Any]:
    """
    Generate preview data for a single room by fetching from S3.
    Returns the preview data ready to be stored in the database.
    """
    room_id = room.get("id")
    room_name = room.get("name")
    source = room.get("source") or ""
    user_id = room.get("userId") or "default"
    profiles = room.get("profiles", [])
    total_profile_count = room.get("total_profile_count", len(profiles))

    room_description_data = fetch_s3_json_safe(room.get("descriptionS3Url"))
    description_summary = room_description_data.get("summary")

    profile_previews = []
    for profile in profiles[:preview_profile_limit]:
        try:
            preview = build_profile_preview(profile, source)
            profile_previews.append(preview)
        except Exception as e:
            logger.warning(f"Failed to build preview for profile {profile.get('id')}: {e}")
            profile_previews.append({
                "id": profile.get("id"),
                "name": profile.get("profileName"),
                "summary": None,
                "source": source.lower() if source else "unknown",
            })

    return {
        "room_id": room_id,
        "name": room_name,
        "user_id": user_id,
        "description_summary": description_summary,
        "source": source.lower() if source else None,
        "total_profile_count": total_profile_count,
        "profiles": profile_previews,
    }
