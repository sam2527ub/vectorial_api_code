"""
Build tier-1/2/3 LinkedIn post JSON for an audience room from S3 profile artifacts.

Call this from `POST .../rebuild-tiered-posts` (or equivalent orchestration) after
room data exists on S3 per profile: posts.json (post list with text, url, isActivity, resharedPost, etc.),
optional comment.json, and profile.json under the same profile folder.
It does not read or write description.json.

Tier 1: authored posts (non-activity or article reshares, URL author matches profile).
Tier 2: reposts and comments with substantive user text (stimulus + response).
Tier 3: sparse or low-signal interactions.

Outputs are uploaded under:
  {enterprise}/linkedin-audience/{room_id}/<tiered_folder>/  (``tiered_folder`` in app/linkedin_tiered_s3/artifacts.yaml)

Before upload, all tier payloads receive a final clean_tiered_blob pass from post_text_polish
(emoji strip, mathematical alphanumerics to ASCII, whitespace).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from app import database
from app.config import s3_bucket
from app.linkedin_tiered_s3 import get_linkedin_tiered_s3_artifact_names
from app.services.linkedin_post_tier_service.post_text_polish import clean_post_text, clean_tiered_blob
from app.utils.s3_utils import get_audience_type_from_source, get_s3_key_for_audience, try_fetch_json_from_s3, upload_json_to_s3

logger = logging.getLogger(__name__)


def _clean_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"http\S+", "", text)
    return text.strip()


def _normalize_post_url(url: Any) -> str:
    if not url or not isinstance(url, str):
        return ""
    return url.split("?", 1)[0].rstrip("/").lower()


def _longest_comment_by_post_url(comment_list: Any) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not isinstance(comment_list, list):
        return out
    for row in comment_list:
        if not isinstance(row, dict):
            continue
        key = _normalize_post_url(row.get("post_url") or row.get("url") or "")
        if not key:
            continue
        body = _clean_text(row.get("comment_body") or row.get("text") or "")
        if not body:
            continue
        prev = out.get(key, "")
        if len(body) > len(prev):
            out[key] = body
    return out


def _comment_list_from_payload(comments_data: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not comments_data or not isinstance(comments_data, dict):
        return []
    raw = comments_data.get("comments", comments_data)
    return raw if isinstance(raw, list) else []


def _accumulate_profile_tiers(
    profile_id: str,
    profile_json: Optional[Dict[str, Any]],
    posts_data: Optional[Dict[str, Any]],
    comments_data: Optional[Dict[str, Any]],
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Returns (tier_1_authored, tier_2_reposts_and_comments, tier_3_sparse) blocks; None if empty."""
    profile_info: Dict[str, str] = {}
    public_id = ""
    if profile_json:
        profile_info = {
            "occupation": profile_json.get("headline", "") or profile_json.get("occupation", "") or "",
        }
        pid = profile_json.get("publicIdentifier", "")
        public_id = pid.lower() if isinstance(pid, str) else ""

    authored_posts: List[Dict[str, str]] = []
    reposts_and_comments: List[Dict[str, str]] = []
    sparse_interactions: List[Dict[str, str]] = []
    activity_counter = 1
    repost_merge_keys: Set[Tuple[str, str]] = set()

    comment_list = _comment_list_from_payload(comments_data)
    url_to_comment_body = _longest_comment_by_post_url(comment_list)

    if posts_data:
        post_list = posts_data.get("posts", posts_data) if isinstance(posts_data, dict) else []
        if not isinstance(post_list, list):
            post_list = []
        for post in post_list:
            if not isinstance(post, dict):
                continue
            main_text = _clean_text(post.get("text", "") or "")
            if not main_text:
                continue

            post_id = f"{profile_id}_{activity_counter}"
            activity_counter += 1

            is_activity = bool(post.get("isActivity", False))
            post_url = (post.get("url", "") or "").lower()
            reshared_post = post.get("resharedPost") or {}
            if not isinstance(reshared_post, dict):
                reshared_post = {}
            is_article = reshared_post.get("type") == "article"

            is_actually_author = True
            if "/posts/" in post_url and public_id:
                try:
                    url_author = post_url.split("/posts/")[1].split("_")[0]
                    if public_id not in url_author:
                        is_actually_author = False
                except (IndexError, AttributeError):
                    pass

            if (not is_activity or is_article) and is_actually_author:
                authored_posts.append({"post_id": post_id, "text": clean_post_text(main_text)})
            else:
                reshared_text = _clean_text(reshared_post.get("text", "") or "")
                if reshared_text:
                    original_post = reshared_text
                    user_text = main_text
                else:
                    original_post = main_text
                    user_text = ""
                    merged = url_to_comment_body.get(_normalize_post_url(post.get("url")))
                    if merged:
                        user_text = merged
                        repost_merge_keys.add((_normalize_post_url(post.get("url")), merged))
                stimulus = clean_post_text(original_post)
                response = clean_post_text(user_text)
                repost_obj = {"post_id": post_id, "stimulus": stimulus, "response": response}
                if reshared_text:
                    is_tier_2_reposts_and_comments = (
                        is_activity and response != stimulus and len(response) > 20
                    )
                else:
                    is_tier_2_reposts_and_comments = is_activity and (
                        len(response) > 60 or (len(response) > 20 and response != stimulus)
                    )
                if is_tier_2_reposts_and_comments:
                    reposts_and_comments.append(repost_obj)
                else:
                    sparse_interactions.append(repost_obj)

    if comment_list:
        for comment in comment_list:
            if not isinstance(comment, dict):
                continue
            main_text = _clean_text(comment.get("text") or comment.get("comment_body") or "")
            if not main_text:
                continue
            comment_url = _normalize_post_url(comment.get("post_url") or comment.get("url") or "")
            if (comment_url, main_text) in repost_merge_keys:
                continue

            post_id = f"{profile_id}_{activity_counter}"
            activity_counter += 1

            parent = comment.get("parentPost")
            parent = parent if isinstance(parent, dict) else {}
            original_post = _clean_text(parent.get("text") or "")
            if not original_post:
                original_post = _clean_text(comment.get("post_body") or "")

            comment_obj = {
                "post_id": post_id,
                "stimulus": clean_post_text(original_post),
                "response": clean_post_text(main_text),
            }

            if len(main_text) > 60:
                reposts_and_comments.append(comment_obj)
            else:
                sparse_interactions.append(comment_obj)

    block_tier_1 = {"profile_info": profile_info, "posts": authored_posts} if authored_posts else None
    block_tier_2 = (
        {"profile_info": profile_info, "interactions": reposts_and_comments}
        if reposts_and_comments
        else None
    )
    block_tier_3 = (
        {"profile_info": profile_info, "interactions": sparse_interactions}
        if sparse_interactions
        else None
    )
    return block_tier_1, block_tier_2, block_tier_3


def rebuild_linkedin_post_tiers_for_room(
    audience_room_id: str,
    enterprise_name: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Read each profile's posts.json, comment.json, and profile.json from S3; compute tiers;
    upload tier files and manifest; basenames from app/linkedin_tiered_s3/artifacts.yaml.

    Manifest uses explicit, tier-specific S3 URL keys (see manifest dict construction below).
    """
    if not database.is_audience_db_available():
        logger.warning("rebuild_linkedin_post_tiers_for_room: audience DB unavailable")
        return None
    if not s3_bucket:
        logger.warning("rebuild_linkedin_post_tiers_for_room: S3 bucket not configured")
        return None

    room = database.find_audience_room_by_id(
        audience_room_id, include_profiles=True, enterprise_name=enterprise_name
    )
    if not room:
        logger.warning("rebuild_linkedin_post_tiers_for_room: room %s not found", audience_room_id)
        return None
    if get_audience_type_from_source(room.source) != "linkedin-audience":
        logger.info(
            "rebuild_linkedin_post_tiers_for_room: skip non-LinkedIn room %s (source=%s)",
            audience_room_id,
            room.source,
        )
        return {"skipped": True, "reason": "not_linkedin_audience"}

    profiles = room.profiles or []
    if not profiles:
        logger.warning("rebuild_linkedin_post_tiers_for_room: room %s has no profiles", audience_room_id)
        return None

    tier_1_authored_by_profile: Dict[str, Any] = {}
    tier_2_reposts_and_comments_by_profile: Dict[str, Any] = {}
    tier_3_sparse_by_profile: Dict[str, Any] = {}

    for profile in profiles:
        pid = profile.id
        posts_key = get_s3_key_for_audience(
            audience_room_id, f"profiles/{pid}/posts.json", enterprise_name, room.source
        )
        comments_key = get_s3_key_for_audience(
            audience_room_id, f"profiles/{pid}/comment.json", enterprise_name, room.source
        )
        profile_key = get_s3_key_for_audience(
            audience_room_id, f"profiles/{pid}/profile.json", enterprise_name, room.source
        )

        posts_data = try_fetch_json_from_s3(posts_key)
        comments_data = try_fetch_json_from_s3(comments_key)
        profile_json = try_fetch_json_from_s3(profile_key)

        block_tier_1, block_tier_2, block_tier_3 = _accumulate_profile_tiers(
            pid, profile_json, posts_data, comments_data
        )
        if block_tier_1:
            tier_1_authored_by_profile[pid] = block_tier_1
        if block_tier_2:
            tier_2_reposts_and_comments_by_profile[pid] = block_tier_2
        if block_tier_3:
            tier_3_sparse_by_profile[pid] = block_tier_3

    # Final pass on aggregated exports (canonical normalized text on S3).
    tier_1_authored_by_profile = clean_tiered_blob(tier_1_authored_by_profile)
    tier_2_reposts_and_comments_by_profile = clean_tiered_blob(tier_2_reposts_and_comments_by_profile)
    tier_3_sparse_by_profile = clean_tiered_blob(tier_3_sparse_by_profile)

    total_tier_1_authored_posts = sum(len(data["posts"]) for data in tier_1_authored_by_profile.values())
    total_tier_2_reposts_and_comments = sum(
        len(data["interactions"]) for data in tier_2_reposts_and_comments_by_profile.values()
    )
    total_tier_3_sparse_interactions = sum(
        len(data["interactions"]) for data in tier_3_sparse_by_profile.values()
    )

    generated_at = datetime.now(timezone.utc).isoformat()
    manifest: Dict[str, Any] = {
        "audience_room_id": audience_room_id,
        "generated_at": generated_at,
        "tier_1_authored_users": len(tier_1_authored_by_profile),
        "tier_1_authored_posts": total_tier_1_authored_posts,
        "tier_2_reposts_and_comments_users": len(tier_2_reposts_and_comments_by_profile),
        "tier_2_reposts_and_comments_interactions": total_tier_2_reposts_and_comments,
        "tier_3_sparse_users": len(tier_3_sparse_by_profile),
        "tier_3_sparse_interactions": total_tier_3_sparse_interactions,
    }

    art = get_linkedin_tiered_s3_artifact_names()
    base = get_s3_key_for_audience(
        audience_room_id, art.tiered_folder, enterprise_name, room.source
    ).rstrip("/")

    def _upload(name: str, payload: Dict[str, Any]) -> str:
        key = f"{base}/{name}"
        return upload_json_to_s3(key, payload)

    tier_1_authored_url = _upload(art.tier_1_authored, tier_1_authored_by_profile)
    tier_2_reposts_and_comments_url = _upload(
        art.tier_2_reposts_and_comments, tier_2_reposts_and_comments_by_profile
    )
    tier_3_sparse_url = _upload(art.tier_3_sparse, tier_3_sparse_by_profile)
    manifest["tier_1_authored_s3_url"] = tier_1_authored_url
    manifest["tier_2_reposts_and_comments_s3_url"] = tier_2_reposts_and_comments_url
    manifest["tier_3_sparse_s3_url"] = tier_3_sparse_url
    manifest_url = _upload(art.manifest, manifest)
    manifest["manifest_s3_url"] = manifest_url

    logger.info(
        "rebuild_linkedin_post_tiers_for_room: room=%s tier_1_posts=%s tier_2_reposts_and_comments=%s "
        "tier_3_sparse=%s manifest=%s",
        audience_room_id,
        total_tier_1_authored_posts,
        total_tier_2_reposts_and_comments,
        total_tier_3_sparse_interactions,
        manifest_url,
    )
    return manifest
