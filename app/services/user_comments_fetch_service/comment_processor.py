"""
Process Apify comment dataset: group by profile, transform to slim, upload comment.json per profile to S3,
and update AudienceProfile.commentsS3Url for each profile in the room. No job table – only profile updates.
"""
import logging
from typing import Any, Dict, List, Optional

from app import database
from app.utils.s3_utils import get_s3_key_for_audience, upload_json_to_s3
from app.services.user_comments_fetch_service.utils.url_normalizer import normalize_profile_url
from app.services.user_comments_fetch_service.utils.comment_transformer import flatten_and_transform_dataset_items
from app.services.user_comments_fetch_service.schemas import SlimComment

logger = logging.getLogger(__name__)


def process_comments_and_update_profiles(
    raw_items: List[Dict[str, Any]],
    audience_room_id: str,
    enterprise_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Group raw comment items by profile (query.profile), transform to slim, upload one comment.json
    per profile to S3 (overwrite), and set AudienceProfile.commentsS3Url for that room's profiles only.
    """
    from app.config import s3_client, s3_bucket

    if not database.is_audience_db_available():
        logger.warning("Audience database not available")
        return {"comments_found": 0, "profiles_updated": 0}

    if not s3_client or not s3_bucket:
        logger.warning("S3 not configured")
        return {"comments_found": 0, "profiles_updated": 0}

    room = database.find_audience_room_by_id(
        audience_room_id, include_profiles=True, enterprise_name=enterprise_name
    )
    if not room or not room.profiles:
        logger.warning("Room %s not found or has no profiles", audience_room_id)
        return {"comments_found": 0, "profiles_updated": 0}

    profile_by_url: Dict[str, Any] = {}
    for p in room.profiles:
        norm = normalize_profile_url(p.profileUrl)
        if norm:
            profile_by_url[norm] = {
                "id": p.id,
                "profileUrl": p.profileUrl,
                "audienceRoomId": p.audienceRoomId,
            }

    slim_all = flatten_and_transform_dataset_items(raw_items)
    # Group by query.profile from original raw items (we need profile URL per item)
    profile_to_items: Dict[str, List[Dict[str, Any]]] = {}
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        query = item.get("query") or {}
        profile_url = normalize_profile_url(query.get("profile"))
        if not profile_url:
            continue
        if profile_url not in profile_to_items:
            profile_to_items[profile_url] = []
        profile_to_items[profile_url].append(item)

    # Build profile_url -> list of slim comments (same order as flattened so we can map)
    # Actually we need to assign each slim comment to a profile. Raw items have query.profile.
    # Flatten again and tag each with profile from parent item.
    profile_to_slim: Dict[str, List[SlimComment]] = {}
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        profile_url = normalize_profile_url((item.get("query") or {}).get("profile"))
        if not profile_url:
            continue
        # This top-level item and its replies all belong to this profile
        flat_local = [item] + (item.get("replies") or [])
        parent_post = item.get("post") or {}
        id_to_body = {
            str(x.get("id")): (x.get("commentary") or "")
            for x in flat_local
            if x.get("id") is not None
        }
        from app.services.user_comments_fetch_service.utils.comment_transformer import transform_to_slim
        for r in flat_local:
            # Ensure replies also have the parent post attached
            if not r.get("post"):
                r["post"] = parent_post
            slim = transform_to_slim(r, id_to_body)
            if profile_url not in profile_to_slim:
                profile_to_slim[profile_url] = []
            profile_to_slim[profile_url].append(slim)

    source = room.source
    updated = 0
    for profile_url, slim_list in profile_to_slim.items():
        profile_info = profile_by_url.get(profile_url)
        if not profile_info:
            continue
        room_id = profile_info["audienceRoomId"]
        pid = profile_info["id"]
        s3_key = get_s3_key_for_audience(
            room_id,
            f"profiles/{pid}/comment.json",
            enterprise_name,
            source,
        )
        payload = {"comments": [c.model_dump() for c in slim_list]}
        try:
            url = upload_json_to_s3(s3_key, payload)
            database.update_audience_profile(pid, {"commentsS3Url": url}, enterprise_name=enterprise_name)
            updated += 1
            logger.info("Updated profile %s commentsS3Url for room %s", pid, room_id)
        except Exception as e:
            logger.exception("Failed to upload comments for profile %s: %s", pid, e)

    return {
        "comments_found": len(slim_all),
        "profiles_updated": updated,
    }
