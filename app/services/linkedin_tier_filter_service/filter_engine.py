"""
Rule-based filters for tier-1 authored posts and tier-2 reposts/comments (in-memory dicts).

Rule-based filter for tier JSON. (Historically similar to the offline filter_1.py in scripts/ — that path is local-only, not used in production.)
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

# Tier 1 (authored posts)
T1_MIN_CHAR_LENGTH = 200
T1_MAX_HASHTAG_RATIO = 0.15
T1_MAX_EMOJI_RATIO = 0.10
T1_MAX_MENTION_RATIO = 0.15
T1_MIN_WORD_COUNT = 40

# Tier 2 (interaction responses)
T2_MIN_CHAR_LENGTH = 80
T2_MAX_HASHTAG_RATIO = 0.20
T2_MAX_EMOJI_RATIO = 0.12
T2_MAX_MENTION_RATIO = 0.20
T2_MIN_WORD_COUNT = 15

NOISE_SIGNALS = [
    "we are hiring", "we're hiring", "we are looking for", "we're looking for",
    "#hiring", "open role", "open position", "job opening", "job opportunity",
    "apply now", "send your cv", "send your resume", "careers page",
    "join our team", "dm me if you", "link in bio",
    "excited to announce", "thrilled to announce", "proud to announce",
    "pleased to announce", "happy to share that", "delighted to share",
    "i am excited to", "i'm excited to",
    "congratulations to", "congrats to", "welcome to the team",
    "wishing you all the best", "shoutout to",
    "check out my new", "my new course", "my new book", "sign up now",
    "limited spots", "registration open", "early bird",
    "see you there", "register here", "link to register",
]

EMOJI_PATTERN = re.compile(
    "["
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F9FF"
    "\U00002700-\U000027BF"
    "\U0001FA00-\U0001FFFF"
    "]+",
    flags=re.UNICODE,
)


def count_emojis(text: str) -> int:
    return sum(len(m.group()) for m in EMOJI_PATTERN.finditer(text))


def rule_filter(
    post_id: str,
    text: str,
    *,
    min_char: int,
    min_words: int,
    max_hashtag_ratio: float,
    max_mention_ratio: float,
    max_emoji_ratio: float,
) -> Tuple[bool, str]:
    stripped = text.strip()

    if len(stripped) < min_char:
        return False, f"Too short: {len(stripped)} chars (min {min_char})"

    tokens = stripped.split()
    real_words = [t for t in tokens if not t.startswith(("#", "@"))]
    if len(real_words) < min_words:
        return False, f"Too few real words: {len(real_words)} (min {min_words})"

    hashtag_count = sum(1 for t in tokens if t.startswith("#"))
    if tokens and hashtag_count / len(tokens) > max_hashtag_ratio:
        return False, f"Hashtag-heavy: {hashtag_count}/{len(tokens)} tokens are hashtags"

    mention_count = sum(1 for t in tokens if t.startswith("@"))
    if tokens and mention_count / len(tokens) > max_mention_ratio:
        return False, f"Mention-heavy: {mention_count}/{len(tokens)} tokens are @mentions"

    emoji_chars = count_emojis(stripped)
    if len(stripped) > 0 and emoji_chars / len(stripped) > max_emoji_ratio:
        return False, f"Emoji-heavy: {emoji_chars} emoji chars in {len(stripped)} total chars"

    lower = stripped.lower()
    for signal in NOISE_SIGNALS:
        if signal in lower:
            return False, f"Noise signal detected: '{signal}'"

    return True, ""


def _flatten_tier1_posts(data: Dict[str, Any]) -> List[Tuple[str, str, str]]:
    flat: List[Tuple[str, str, str]] = []
    for user_id, block in data.items():
        if not isinstance(block, dict):
            continue
        for post in block.get("posts") or []:
            if not isinstance(post, dict):
                continue
            pid = (post.get("post_id") or "").strip()
            text = (post.get("text") or "").strip()
            if not pid or not text:
                continue
            flat.append((str(user_id), pid, text))
    return flat


def _flatten_tier2_interactions(data: Dict[str, Any]) -> List[Tuple[str, str, str, str]]:
    flat: List[Tuple[str, str, str, str]] = []
    for user_id, block in data.items():
        if not isinstance(block, dict):
            continue
        for row in block.get("interactions") or []:
            if not isinstance(row, dict):
                continue
            pid = (row.get("post_id") or "").strip()
            stimulus = (row.get("stimulus") or row.get("original_post") or "").strip()
            response = (row.get("response") or row.get("user_text") or "").strip()
            if not pid or not response:
                continue
            flat.append((str(user_id), pid, response, stimulus))
    return flat


def filter_tier1_authored_payload(
    raw_data: Dict[str, Any],
    *,
    source_name: Optional[str] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, int]]:
    """
    Returns (kept_payload, dropped_payload, stats) with same shape as filter_1.py on disk.
    kept_payload has keys: source_file, stats, users.

    ``source_name`` defaults to the tier-1 unfiltered object basename in
    ``app/linkedin_tiered_s3/artifacts.yaml``.
    """
    if source_name is None:
        from app.linkedin_tiered_s3 import get_linkedin_tiered_s3_artifact_names

        source_name = get_linkedin_tiered_s3_artifact_names().tier_1_authored
    if not isinstance(raw_data, dict):
        raw_data = {}
    all_posts = _flatten_tier1_posts(raw_data)
    kept_by_user: Dict[str, Any] = {}
    dropped: List[Dict[str, Any]] = []

    for user_id, post_id, text in all_posts:
        keep, reason = rule_filter(
            post_id,
            text,
            min_char=T1_MIN_CHAR_LENGTH,
            min_words=T1_MIN_WORD_COUNT,
            max_hashtag_ratio=T1_MAX_HASHTAG_RATIO,
            max_mention_ratio=T1_MAX_MENTION_RATIO,
            max_emoji_ratio=T1_MAX_EMOJI_RATIO,
        )
        if keep:
            if user_id not in kept_by_user:
                block = raw_data.get(user_id, {})
                if not isinstance(block, dict):
                    block = {}
                kept_by_user[user_id] = {
                    "profile_info": block.get("profile_info") or {},
                    "posts": [],
                }
            kept_by_user[user_id]["posts"].append({"post_id": post_id, "text": text})
        else:
            dropped.append(
                {
                    "user_id": user_id,
                    "post_id": post_id,
                    "reason": reason,
                    "text_preview": text[:150],
                }
            )

    total_seen = len(all_posts)
    total_kept = sum(
        len(b.get("posts") or b.get("interactions") or [])
        for b in kept_by_user.values()
        if isinstance(b, dict)
    )
    total_dropped = len(dropped)
    stats = {
        "total_seen": total_seen,
        "kept": total_kept,
        "dropped": total_dropped,
        "keep_rate": f"{total_kept / total_seen * 100:.1f}%" if total_seen else "0%",
    }
    kept_payload = {
        "source_file": source_name,
        "stats": stats,
        "users": kept_by_user,
    }
    dropped_payload = {"dropped_posts": dropped}
    return kept_payload, dropped_payload, {
        "total_seen": total_seen,
        "kept": total_kept,
        "dropped": total_dropped,
    }


def filter_tier2_reposts_and_comments_payload(
    raw_data: Dict[str, Any],
    *,
    source_name: Optional[str] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, int]]:
    """Same contract as filter_1.py tier 2: kept has users -> interactions with stimulus/response.

    ``source_name`` defaults to the tier-2 unfiltered object basename in
    ``app/linkedin_tiered_s3/artifacts.yaml``.
    """
    if source_name is None:
        from app.linkedin_tiered_s3 import get_linkedin_tiered_s3_artifact_names

        source_name = get_linkedin_tiered_s3_artifact_names().tier_2_reposts_and_comments
    if not isinstance(raw_data, dict):
        raw_data = {}
    all_rows = _flatten_tier2_interactions(raw_data)
    kept_by_user: Dict[str, Any] = {}
    dropped: List[Dict[str, Any]] = []

    for user_id, post_id, response, stimulus in all_rows:
        keep, reason = rule_filter(
            post_id,
            response,
            min_char=T2_MIN_CHAR_LENGTH,
            min_words=T2_MIN_WORD_COUNT,
            max_hashtag_ratio=T2_MAX_HASHTAG_RATIO,
            max_mention_ratio=T2_MAX_MENTION_RATIO,
            max_emoji_ratio=T2_MAX_EMOJI_RATIO,
        )
        if keep:
            if user_id not in kept_by_user:
                block = raw_data.get(user_id, {})
                if not isinstance(block, dict):
                    block = {}
                kept_by_user[user_id] = {
                    "profile_info": block.get("profile_info") or {},
                    "interactions": [],
                }
            kept_by_user[user_id]["interactions"].append(
                {
                    "post_id": post_id,
                    "stimulus": stimulus,
                    "response": response,
                }
            )
        else:
            dropped.append(
                {
                    "user_id": user_id,
                    "post_id": post_id,
                    "reason": reason,
                    "text_preview": response[:150],
                }
            )

    total_seen = len(all_rows)
    total_kept = sum(
        len(b.get("posts") or b.get("interactions") or [])
        for b in kept_by_user.values()
        if isinstance(b, dict)
    )
    total_dropped = len(dropped)
    stats = {
        "total_seen": total_seen,
        "kept": total_kept,
        "dropped": total_dropped,
        "keep_rate": f"{total_kept / total_seen * 100:.1f}%" if total_seen else "0%",
    }
    kept_payload = {
        "source_file": source_name,
        "stats": stats,
        "users": kept_by_user,
    }
    dropped_payload = {"dropped_posts": dropped}
    return kept_payload, dropped_payload, {
        "total_seen": total_seen,
        "kept": total_kept,
        "dropped": total_dropped,
    }
