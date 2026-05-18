"""Load tier-1 / tier-2 filter JSON; build flat rows (same as local extract_stimulus_category script)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from .errors import StimulusExtractionError


def _iter_user_blocks(raw: Dict[str, Any]) -> Iterable[Tuple[str, Dict[str, Any]]]:
    """Handle filter output: ``users`` or ``results_by_user``."""
    ru = raw.get("results_by_user")
    if isinstance(ru, dict):
        for uid, bundle in ru.items():
            if isinstance(bundle, dict):
                yield str(uid), bundle
        return
    users = raw.get("users")
    if isinstance(users, dict):
        for uid, bundle in users.items():
            if isinstance(bundle, dict):
                yield str(uid), bundle
        return
    skip = {"room_id", "metadata", "results_by_user", "users", "source_file", "stats"}
    for uid, bundle in raw.items():
        if uid in skip:
            continue
        if isinstance(bundle, dict):
            yield str(uid), bundle


def load_tier1_flat(tier_data: Dict[str, Any]) -> List[Tuple[str, str, str]]:
    """Return rows (user_id, post_id, text)."""
    flat: List[Tuple[str, str, str]] = []
    for user_id, block in _iter_user_blocks(tier_data):
        for post in block.get("posts") or []:
            if not isinstance(post, dict):
                continue
            pid = (post.get("post_id") or "") or ""
            text = (post.get("text") or "").strip()
            if not pid and not text:
                continue
            if not text:
                continue
            flat.append((str(user_id), str(pid), text))
    return flat


def build_results_skeleton_posts(tier_data: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for uid, block in _iter_user_blocks(tier_data):
        out[str(uid)] = {
            "profile_info": (block or {}).get("profile_info") or {},
            "posts": [],
        }
    return out


def load_tier2_flat(
    tier_data: Dict[str, Any],
) -> List[Tuple[str, str, str, str]]:
    """Rows: user_id, post_id, response, stimulus (thread / original post)."""
    flat: List[Tuple[str, str, str, str]] = []
    for user_id, block in _iter_user_blocks(tier_data):
        rows = (block or {}).get("interactions") or (block or {}).get("posts") or []
        for row in rows:
            if not isinstance(row, dict):
                continue
            pid = (row.get("post_id") or "").strip()
            stimulus = (row.get("stimulus") or row.get("original_post") or "").strip()
            response = (
                row.get("response") or row.get("user_text") or row.get("text") or ""
            ).strip()
            if not pid or not response:
                continue
            flat.append((str(user_id), str(pid), response, stimulus))
    return flat


def load_json_dict_from_path(path: Path) -> Dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise StimulusExtractionError(f"Expected object JSON, got {type(raw).__name__}")
    return raw


def load_allowed_categories_from_mapping(data: Dict[str, Any]) -> List[str]:
    cats = data.get("categories")
    if not isinstance(cats, list):
        raise StimulusExtractionError('Theme mapping JSON must contain a "categories" array')
    out: List[str] = []
    for c in cats:
        if isinstance(c, dict):
            v = c.get("category", c)
            if v is not None and str(v).strip():
                out.append(str(v).strip())
        elif c is not None and str(c).strip():
            out.append(str(c).strip())
    if not out:
        raise StimulusExtractionError("Theme mapping has no non-empty category labels")
    return out
