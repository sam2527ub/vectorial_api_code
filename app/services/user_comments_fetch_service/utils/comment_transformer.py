"""
Transform raw Apify comment items to slim schema.
Top-level comments get parent_comment_body = None; replies get parent from id->body map.
"""
import re
from typing import Any, Dict, List, Optional

from app.services.user_comments_fetch_service.schemas import SlimComment


def _extract_parent_comment_id_from_linkedin_url(linkedin_url: Optional[str]) -> Optional[str]:
    """
    For a reply, commentUrn in the URL is (entity, parentCommentId).
    Extract parent comment id from commentUrn when replyUrn is present.
    """
    if not linkedin_url or "replyUrn=" not in linkedin_url:
        return None
    try:
        # commentUrn=urn%3Ali%3Acomment%3A%28ugcPost%3A7423926230049087488%2C7424017270898786304%29
        match = re.search(r"commentUrn=([^&]+)", linkedin_url)
        if not match:
            return None
        from urllib.parse import unquote
        urn = unquote(match.group(1))
        # urn:li:comment:(ugcPost:POST_ID,PARENT_COMMENT_ID) for reply; second number is parent
        m = re.search(r"comment:\([^,]+,(\d+)\)", urn)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def _flatten_items(raw_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flatten top-level items and nested replies into a single list."""
    flat: List[Dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        flat.append(item)
        for reply in item.get("replies") or []:
            if isinstance(reply, dict):
                flat.append(reply)
    return flat


def _build_id_to_body_map(flat_items: List[Dict[str, Any]]) -> Dict[str, str]:
    """Map comment id -> commentary (body) for parent lookup."""
    out: Dict[str, str] = {}
    for it in flat_items:
        cid = it.get("id")
        body = it.get("commentary")
        if cid is not None and body is not None:
            out[str(cid)] = body if isinstance(body, str) else str(body)
    return out


def transform_to_slim(
    item: Dict[str, Any],
    id_to_body: Dict[str, str],
) -> SlimComment:
    """
    Convert one raw comment item to SlimComment.
    Top-level (no replyUrn in linkedinUrl) -> parent_comment_body = None.
    Reply -> parent_comment_body from id_to_body if parent in dataset else None.
    """
    linkedin_url = item.get("linkedinUrl") or ""
    commentary = item.get("commentary")
    comment_body = (commentary if isinstance(commentary, str) else str(commentary or "")).strip()
    post = item.get("post") or {}
    post_url = post.get("linkedinUrl") or ""
    post_content = post.get("content")
    post_body = (post_content if isinstance(post_content, str) else str(post_content or "")).strip()

    parent_comment_body: Optional[str] = None
    if "replyUrn=" in (linkedin_url or ""):
        parent_id = _extract_parent_comment_id_from_linkedin_url(linkedin_url)
        if parent_id and parent_id in id_to_body:
            parent_comment_body = id_to_body[parent_id]
    # else: top-level item -> parent_comment_body stays None

    return SlimComment(
        comment_url=linkedin_url or "",
        post_url=post_url or "",
        comment_body=comment_body,
        post_body=post_body,
        parent_comment_body=parent_comment_body,
    )


def flatten_and_transform_dataset_items(raw_items: List[Dict[str, Any]]) -> List[SlimComment]:
    """Flatten (top-level + replies), build id->body map, transform each to slim. Top-level => parent_comment_body null."""
    flat = _flatten_items(raw_items)
    id_to_body = _build_id_to_body_map(flat)
    return [transform_to_slim(it, id_to_body) for it in flat]
