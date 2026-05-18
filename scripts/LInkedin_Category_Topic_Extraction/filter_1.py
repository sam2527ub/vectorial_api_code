#!/usr/bin/env python3
"""
Rule-based filter for tier-1 authored posts or tier-2 reposts and comments (local / offline).

Tier 1: reads tier_1_authored_cleaned.json, filters post.text, writes filtered_kept.json.
Tier 2: reads tier_2_reposts_and_comments.json, filters interaction.response, writes filtered_kept_tier2.json.

For audience rooms served by the API, run filtering via POST .../filter-tiered-posts after rebuild-tiered-posts.
This script stays self-contained for batch work under scripts/ (same rules as app filter_engine — keep in sync manually).

Run (from this directory or with paths as needed):
  python filter_1.py              # tier 1 (default)
  python filter_1.py --tier 2   # tier 2 interactions
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
POSTS_EXTRACTION = SCRIPT_DIR.parent / "Linkedin_Posts_Extraction" / "tiered_posts_grouped"
OUTPUT_DIR = SCRIPT_DIR / "tiered_posts_grouped"

# --- Tier 1 thresholds (authored posts) ---
T1_MIN_CHAR_LENGTH = 200
T1_MAX_HASHTAG_RATIO = 0.15
T1_MAX_EMOJI_RATIO = 0.10
T1_MAX_MENTION_RATIO = 0.15
T1_MIN_WORD_COUNT = 40

# --- Tier 2 thresholds (member replies; usually shorter) ---
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
) -> tuple[bool, str]:
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


def load_tier1_posts(path: Path) -> tuple[dict, list[tuple[str, str, str]]]:
    """(raw tier data, flat list of (user_id, post_id, text))."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    flat: list[tuple[str, str, str]] = []
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
            flat.append((user_id, pid, text))
    return data, flat


def load_tier2_interactions(
    path: Path,
) -> tuple[dict, list[tuple[str, str, str, str]]]:
    """(raw tier data, flat rows: user_id, post_id, response, stimulus)."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    flat: list[tuple[str, str, str, str]] = []
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
            flat.append((user_id, pid, response, stimulus))
    return data, flat


def run_filter_tier1(input_path: Path, kept_path: Path, dropped_path: Path) -> None:
    raw_data, all_posts = load_tier1_posts(input_path)
    kept_by_user: dict = {}
    dropped: list[dict] = []

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

    _save_outputs(
        input_path,
        kept_path,
        dropped_path,
        kept_by_user,
        dropped,
        len(all_posts),
        key_users="users",
    )


def run_filter_tier2(input_path: Path, kept_path: Path, dropped_path: Path) -> None:
    raw_data, all_rows = load_tier2_interactions(input_path)
    kept_by_user: dict = {}
    dropped: list[dict] = []

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
    _save_outputs(
        input_path,
        kept_path,
        dropped_path,
        kept_by_user,
        dropped,
        total_seen,
        key_users="users",
    )


def _save_outputs(
    input_path: Path,
    kept_path: Path,
    dropped_path: Path,
    kept_by_user: dict,
    dropped: list[dict],
    total_seen: int,
    *,
    key_users: str,
) -> None:
    total_kept = sum(
        len(b.get("posts") or b.get("interactions") or [])
        for b in kept_by_user.values()
        if isinstance(b, dict)
    )
    total_dropped = len(dropped)
    kept_path.parent.mkdir(parents=True, exist_ok=True)
    kept_payload = {
        "source_file": input_path.name,
        "stats": {
            "total_seen": total_seen,
            "kept": total_kept,
            "dropped": total_dropped,
            "keep_rate": f"{total_kept / total_seen * 100:.1f}%" if total_seen else "0%",
        },
        key_users: kept_by_user,
    }
    with open(kept_path, "w", encoding="utf-8") as f:
        json.dump(kept_payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    with open(dropped_path, "w", encoding="utf-8") as f:
        json.dump({"dropped_posts": dropped}, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print("\n✅ Filter complete")
    print(f"   Input   : {input_path}")
    print(f"   Kept    : {total_kept}")
    print(f"   Dropped : {total_dropped}")
    print(f"   Keep rate: {total_kept / total_seen * 100:.1f}%" if total_seen else "")
    print(f"\n   Kept    → {kept_path}")
    print(f"   Dropped → {dropped_path}")
    if dropped:
        print("\n--- Sample drops (first 5) ---")
        for d in dropped[:5]:
            print(f"  [{d['post_id']}] {d['reason']}")


def main() -> None:
    p = argparse.ArgumentParser(description="Rule-based filter for tier-1 posts or tier-2 interactions.")
    p.add_argument("--tier", choices=("1", "2"), default="1", help="1 = authored posts, 2 = tier-2 interactions")
    p.add_argument(
        "--input",
        type=str,
        default=None,
        help="Override input JSON path (default: tier_1_authored_cleaned or tier_2_reposts_and_comments).",
    )
    p.add_argument("--output-dir", type=str, default=str(OUTPUT_DIR), help="Directory for kept/dropped JSON")
    args = p.parse_args()
    out_dir = Path(args.output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.tier == "1":
        default_in = POSTS_EXTRACTION / "tier_1_authored_cleaned.json"
        inp = Path(args.input).expanduser().resolve() if args.input else default_in
        kept = out_dir / "filtered_kept.json"
        dropped = out_dir / "filtered_dropped.json"
        print("🔍 Rule-based filter — tier 1 (authored posts)")
        print(f"   Input: {inp}")
        if not inp.is_file():
            print(f"❌ Input not found: {inp}")
            return
        run_filter_tier1(inp, kept, dropped)
    else:
        default_in = POSTS_EXTRACTION / "tier_2_reposts_and_comments.json"
        inp = Path(args.input).expanduser().resolve() if args.input else default_in
        kept = out_dir / "filtered_kept_tier2.json"
        dropped = out_dir / "filtered_dropped_tier2.json"
        print("🔍 Rule-based filter — tier 2 (member interaction responses)")
        print(f"   Input: {inp}")
        if not inp.is_file():
            print(f"❌ Input not found: {inp}")
            return
        run_filter_tier2(inp, kept, dropped)


if __name__ == "__main__":
    main()
