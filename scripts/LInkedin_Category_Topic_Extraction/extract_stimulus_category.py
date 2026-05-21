#!/usr/bin/env python3
"""
Tier-1 **stimulus + category** (both from the LLM) vs tier-2 **category only** (LLM).

Tier 1: ``filtered_kept.json`` → ``contextual_stimulus_extraction.json``.
        Each post: LLM returns a short neutral **stimulus** label + one **category**.

Tier 2: ``filtered_kept_tier2.json`` → ``contextual_stimulus_extraction_tier2.json``.
        Same output **shape** as tier 1 (``post_id``, ``body_text``, ``stimulus``, ``category``).
        ``body_text`` = member reply; **no LLM stimulus step** — ``stimulus`` is copied verbatim
        from the input row (parent/original post text) for downstream compatibility only.
        The LLM returns **only** ``category``, optionally using that thread text as
        ``thread_context`` in the prompt to disambiguate the reply.

**Allowed category labels** come from the discovered mapping JSON (``categories`` array):
tier 1 default: ``discovered_category_topic_mapping.json``;
tier 2 default: ``discovered_category_topic_mapping_tier2.json`` if present, else tier 1 file
(with a console warning). Override with ``--mapping`` if needed.

Run:
  python extract_stimulus_category.py           # tier 1
  python extract_stimulus_category.py --tier 2  # tier 2 (category-only LLM)
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

from openai import OpenAI
from pydantic import BaseModel, Field

SCRIPT_DIR = Path(__file__).resolve().parent
EXTRACTION_GROUPED = SCRIPT_DIR / "tiered_posts_grouped"
TIER1_DISCOVERED_MAPPING = EXTRACTION_GROUPED / "discovered_category_topic_mapping.json"
TIER2_DISCOVERED_MAPPING = EXTRACTION_GROUPED / "discovered_category_topic_mapping_tier2.json"

TARGET_ROOM_ID = "91cd222b-8cc9-4ee4-bf06-c5e9ed2f6f74"

PRICE_INPUT_1M = 0.15
PRICE_OUTPUT_1M = 0.60
BATCH_SIZE = 5


class PostAnalysis(BaseModel):
    post_id: str
    stimulus: str = Field(
        description="Neutral domain label for routing (not the verbatim reply topic)."
    )
    category: str = Field(description="Exactly one category from the provided mapping.")


class BatchResponse(BaseModel):
    extracted_data: List[PostAnalysis]


class InteractionCategoryOnly(BaseModel):
    post_id: str
    category: str = Field(description="Exactly one category from the provided mapping.")


class BatchCategoryOnly(BaseModel):
    extracted_data: List[InteractionCategoryOnly]


def _iter_user_blocks(raw: Dict[str, Any]):
    """Handle raw tier JSON or filter output wrapped in users/results_by_user."""
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


def load_tier1_posts(tier_path: Path) -> Tuple[Dict[str, Any], List[Tuple[str, str, str]]]:
    with open(tier_path, encoding="utf-8") as f:
        tier_data = json.load(f)
    flat: List[Tuple[str, str, str]] = []
    for user_id, block in _iter_user_blocks(tier_data):
        for post in block.get("posts") or []:
            if not isinstance(post, dict):
                continue
            pid = post.get("post_id") or ""
            text = (post.get("text") or "").strip()
            if not pid and not text:
                continue
            flat.append((user_id, pid, text))
    return tier_data, flat


def load_tier2_interactions(
    tier_path: Path,
) -> Tuple[Dict[str, Any], List[Tuple[str, str, str, str]]]:
    """Flat rows: user_id, post_id, response, stimulus (thread / original post text)."""
    with open(tier_path, encoding="utf-8") as f:
        tier_data = json.load(f)
    flat: List[Tuple[str, str, str, str]] = []
    for user_id, block in _iter_user_blocks(tier_data):
        rows = block.get("interactions") or block.get("posts") or []
        for row in rows:
            if not isinstance(row, dict):
                continue
            pid = (row.get("post_id") or "").strip()
            stimulus = (row.get("stimulus") or row.get("original_post") or "").strip()
            response = (row.get("response") or row.get("user_text") or row.get("text") or "").strip()
            if not pid or not response:
                continue
            flat.append((user_id, pid, response, stimulus))
    return tier_data, flat


def build_results_skeleton_posts(tier_data: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for uid, block in _iter_user_blocks(tier_data):
        out[uid] = {
            "profile_info": block.get("profile_info") or {},
            "posts": [],
        }
    return out


def compute_cost(in_tokens: int, out_tokens: int) -> float:
    return (in_tokens / 1_000_000) * PRICE_INPUT_1M + (out_tokens / 1_000_000) * PRICE_OUTPUT_1M


def save_output(
    path: Path,
    *,
    room_id: str,
    source_file: str,
    results_by_user: Dict[str, Any],
    total_posts_processed: int,
    total_in_tokens: int,
    total_out_tokens: int,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cost = compute_cost(total_in_tokens, total_out_tokens)
    payload = {
        "room_id": room_id,
        "source_file": source_file,
        "results_by_user": results_by_user,
        "total_posts_processed": total_posts_processed,
        "total_prompt_tokens": total_in_tokens,
        "total_completion_tokens": total_out_tokens,
        "cost_usd": round(cost, 6),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")


def resolve_category_mapping_path(explicit: Path | None, *, tier: str) -> Path:
    """Tier 1 → tier1 discovered; tier 2 → tier2 discovered if present, else tier1 (warn)."""
    if explicit is not None:
        p = explicit.expanduser().resolve()
        if not p.is_file():
            raise FileNotFoundError(f"Mapping not found: {p}")
        return p
    if tier == "2":
        if TIER2_DISCOVERED_MAPPING.is_file():
            return TIER2_DISCOVERED_MAPPING
        if TIER1_DISCOVERED_MAPPING.is_file():
            print(
                f"⚠️ Tier-2 mapping not found ({TIER2_DISCOVERED_MAPPING.name}); "
                f"using {TIER1_DISCOVERED_MAPPING.name} — category names may not match tier-2 topic discovery."
            )
            return TIER1_DISCOVERED_MAPPING
        raise FileNotFoundError(
            f"Category mapping not found: need {TIER2_DISCOVERED_MAPPING} or {TIER1_DISCOVERED_MAPPING} "
            "(or pass --mapping)"
        )
    if not TIER1_DISCOVERED_MAPPING.is_file():
        raise FileNotFoundError(
            f"Category mapping not found: {TIER1_DISCOVERED_MAPPING} (pass --mapping to a valid JSON)"
        )
    return TIER1_DISCOVERED_MAPPING


def _load_allowed_categories(mapping_path: Path) -> List[str]:
    with open(mapping_path, encoding="utf-8") as f:
        mapping_data = json.load(f)
    cats = mapping_data.get("categories")
    if not isinstance(cats, list):
        raise ValueError("mapping JSON missing categories array")
    return [str(c.get("category", c) if isinstance(c, dict) else c) for c in cats]


def extract_tier1(tier_path: Path, output_path: Path, mapping_path: Path) -> None:
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    allowed_categories = _load_allowed_categories(mapping_path)
    tier_data, all_posts = load_tier1_posts(tier_path)
    if not all_posts:
        print("❌ No posts found in tier file.")
        return

    results_by_user = build_results_skeleton_posts(tier_data)
    total_in_tokens = 0
    total_out_tokens = 0
    total_processed = 0

    system_instruction = (
        "You are a structured data extraction engine. You strictly distinguish between "
        "environmental context (Stimulus) and the post's subject for category routing. "
        "Return only post_id, stimulus, and category for each post."
    )

    def get_user_prompt(batch: List[Dict[str, str]]) -> str:
        return f"""
Analyze these LinkedIn posts. Return structured data for each post.

━━━━━━━━━━━━━━━━━━━━
### 1. STIMULUS
━━━━━━━━━━━━━━━━━━━━
The stimulus is a neutral domain label — the broad topic space
this post belongs to, described independently of what the author
said, felt, announced, or reacted to.

Think of it like a product category label on a shelf.
It tells you the domain. Nothing about the specific item.
It must not reveal:
  - What the author wrote about
  - What event, announcement, or action is in the post
  - Any detail that would make the category obvious
If the post makes an argument or shares an opinion,
describe the domain the argument lives in — not the argument itself.
It must be:
-  - A short noun phrase (10-15 words)
  - Broad enough that multiple categories could plausibly apply
  - Specific enough to be meaningfully distinct from other domains
  - Free of verbs, emotions, author references, and post content

━━━━━━━━━━━━━━━━━━━━
### 2. CATEGORY
━━━━━━━━━━━━━━━━━━━━
Assign exactly one category from the list below based on the core subject of the post body.

### ALLOWED CATEGORIES:
{json.dumps(allowed_categories, indent=2)}

### POSTS (post_id + body_text)
{json.dumps(batch, ensure_ascii=False, indent=2)}
"""

    source_name = tier_path.name
    for batch_start in range(0, len(all_posts), BATCH_SIZE):
        chunk = all_posts[batch_start : batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1
        api_batch = [{"post_id": pid, "body_text": body} for (_uid, pid, body) in chunk]
        print(
            f"\n📦 Batch {batch_num} — posts {batch_start + 1}–{batch_start + len(chunk)} of {len(all_posts)}"
        )
        try:
            completion = client.beta.chat.completions.parse(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": get_user_prompt(api_batch)},
                ],
                response_format=BatchResponse,
            )
        except Exception as e:
            print(f"⚠️ Error in batch {batch_num}: {e}")
            continue

        usage = completion.usage
        if usage:
            total_in_tokens += usage.prompt_tokens or 0
            total_out_tokens += usage.completion_tokens or 0

        parsed = completion.choices[0].message.parsed
        if not parsed:
            print(f"⚠️ Empty parse for batch {batch_num}")
            continue

        by_post_id = {item.post_id: item for item in parsed.extracted_data}
        batch_records: List[Dict[str, Any]] = []
        for user_id, post_id, body_text in chunk:
            item = by_post_id.get(post_id)
            if not item:
                print(f"⚠️ Missing model row for post_id={post_id!r}")
                continue
            record = {
                "post_id": post_id,
                "body_text": body_text,
                "stimulus": item.stimulus,
                "category": item.category,
            }
            results_by_user[user_id]["posts"].append(record)
            batch_records.append(record)
            total_processed += 1

        print(f"--- Batch {batch_num} output ({len(batch_records)} posts) ---")
        print(json.dumps(batch_records, ensure_ascii=False, indent=2))
        save_output(
            output_path,
            room_id=TARGET_ROOM_ID,
            source_file=source_name,
            results_by_user=results_by_user,
            total_posts_processed=total_processed,
            total_in_tokens=total_in_tokens,
            total_out_tokens=total_out_tokens,
        )
        print(f"💾 Saved checkpoint → {output_path} (total processed: {total_processed})")

    print(f"\n✅ Done. Posts processed: {total_processed}. Cost (USD): {compute_cost(total_in_tokens, total_out_tokens):.6f}")
    print(f"📂 {output_path}")


def extract_tier2(tier_path: Path, output_path: Path, mapping_path: Path) -> None:
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    allowed_categories = _load_allowed_categories(mapping_path)
    tier_data, all_rows = load_tier2_interactions(tier_path)
    if not all_rows:
        print("❌ No tier-2 interactions with non-empty response.")
        return

    results_by_user = build_results_skeleton_posts(tier_data)
    total_in_tokens = 0
    total_out_tokens = 0
    total_processed = 0

    system_instruction = (
        "You assign exactly one taxonomy category per LinkedIn **member reply** (body_text). "
        "thread_context is the original post text for disambiguation only — do not output it. "
        "Category must reflect what the reply is mainly about. Return post_id and category only."
    )

    def get_user_prompt(batch: List[Dict[str, str]]) -> str:
        return f"""
For each row, use ``thread_context`` only to disambiguate; assign **category** from ``body_text`` (the reply).

### ALLOWED CATEGORIES:
{json.dumps(allowed_categories, indent=2)}

### INTERACTIONS (post_id, body_text = reply, thread_context = original post)
{json.dumps(batch, ensure_ascii=False, indent=2)}
"""

    source_name = tier_path.name
    print("   (LLM: category only | stimulus column = passthrough from input, not generated)")
    for batch_start in range(0, len(all_rows), BATCH_SIZE):
        chunk = all_rows[batch_start : batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1
        api_batch = []
        for _uid, pid, response, stimulus in chunk:
            api_batch.append(
                {
                    "post_id": pid,
                    "body_text": response,
                    "thread_context": (stimulus[:6000] + "…") if len(stimulus) > 6000 else stimulus,
                }
            )
        print(
            f"\n📦 Batch {batch_num} — interactions {batch_start + 1}–{batch_start + len(chunk)} of {len(all_rows)}"
        )
        try:
            completion = client.beta.chat.completions.parse(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": get_user_prompt(api_batch)},
                ],
                response_format=BatchCategoryOnly,
            )
        except Exception as e:
            print(f"⚠️ Error in batch {batch_num}: {e}")
            continue

        usage = completion.usage
        if usage:
            total_in_tokens += usage.prompt_tokens or 0
            total_out_tokens += usage.completion_tokens or 0

        parsed = completion.choices[0].message.parsed
        if not parsed:
            print(f"⚠️ Empty parse for batch {batch_num}")
            continue

        by_post_id = {item.post_id: item for item in parsed.extracted_data}
        batch_records: List[Dict[str, Any]] = []
        for user_id, post_id, response, stimulus in chunk:
            item = by_post_id.get(post_id)
            if not item:
                print(f"⚠️ Missing model row for post_id={post_id!r}")
                continue
            # Stimulus must remain identical to the input row (never from the LLM).
            record = {
                "post_id": post_id,
                "body_text": response,
                "stimulus": stimulus,
                "category": item.category,
            }
            results_by_user[user_id]["posts"].append(record)
            batch_records.append(record)
            total_processed += 1

        print(f"--- Batch {batch_num} output ({len(batch_records)} rows) ---")
        print(json.dumps(batch_records, ensure_ascii=False, indent=2))
        save_output(
            output_path,
            room_id=TARGET_ROOM_ID,
            source_file=source_name,
            results_by_user=results_by_user,
            total_posts_processed=total_processed,
            total_in_tokens=total_in_tokens,
            total_out_tokens=total_out_tokens,
        )
        print(f"💾 Saved checkpoint → {output_path} (total processed: {total_processed})")

    print(f"\n✅ Done. Interactions processed: {total_processed}. Cost (USD): {compute_cost(total_in_tokens, total_out_tokens):.6f}")
    print(f"📂 {output_path}")


def main() -> None:
    p = argparse.ArgumentParser(
        description=(
            "Tier 1: LLM stimulus + category from authored posts. "
            "Tier 2: LLM category only from member replies (same output JSON shape; stimulus passthrough)."
        )
    )
    p.add_argument("--tier", choices=("1", "2"), default="1")
    p.add_argument(
        "--input",
        type=str,
        default=None,
        help="Override tier JSON path (tier 1 default: tiered_posts_grouped/filtered_kept.json; tier 2: filtered_kept_tier2.json)",
    )
    p.add_argument("--output", type=str, default=None, help="Override output JSON path")
    p.add_argument(
        "--mapping",
        type=str,
        default=None,
        help=(
            "Category list JSON (categories[]). Default: tier1 → discovered_category_topic_mapping.json; "
            "tier2 → discovered_category_topic_mapping_tier2.json if present, else tier1 file."
        ),
    )
    args = p.parse_args()

    mapping_arg = Path(args.mapping).expanduser().resolve() if args.mapping else None
    try:
        mapping_path = resolve_category_mapping_path(mapping_arg, tier=args.tier)
    except FileNotFoundError as e:
        print(f"❌ {e}")
        return

    if args.tier == "1":
        tier_path = (
            Path(args.input).expanduser().resolve()
            if args.input
            else EXTRACTION_GROUPED / "filtered_kept.json"
        )
        out = (
            Path(args.output).expanduser().resolve()
            if args.output
            else EXTRACTION_GROUPED / "contextual_stimulus_extraction.json"
        )
        print("⚙️ Tier 1 — stimulus + category (LLM) for authored posts")
        print(f"   Input: {tier_path}")
        print(f"   Category mapping: {mapping_path}")
        if not tier_path.is_file():
            print(f"❌ Tier file not found: {tier_path}")
            return
        extract_tier1(tier_path, out, mapping_path)
    else:
        tier_path = (
            Path(args.input).expanduser().resolve()
            if args.input
            else EXTRACTION_GROUPED / "filtered_kept_tier2.json"
        )
        out = (
            Path(args.output).expanduser().resolve()
            if args.output
            else EXTRACTION_GROUPED / "contextual_stimulus_extraction_tier2.json"
        )
        print("⚙️ Tier 2 — category only (LLM) for member replies; stimulus = passthrough from input")
        print(f"   Input: {tier_path}")
        print(f"   Category mapping: {mapping_path}")
        if not tier_path.is_file():
            print(f"❌ Tier file not found: {tier_path} (run filter_1.py --tier 2 first)")
            return
        extract_tier2(tier_path, out, mapping_path)


if __name__ == "__main__":
    main()
