#!/usr/bin/env python3

"""
Discover topic labels from LinkedIn tier JSON (map-reduce style), then
assign topics into a small set of broad categories — all local I/O, OpenAI API only.

Topics are extracted as short noun phrases (4–8 words) that carry the human
tension or feeling around the subject — not dry domain labels, not narrative headlines.

1. Load tier 1 style bundles: { user_id: { "posts": [...] } } or
   { "results_by_user": { ... } } (e.g. contextual stimulus exports).
   Post body is read from `text` or `body_text`. Tier 2 is optional (--tier2).

2. Map rounds: random batches of post text → LLM returns JSON {"themes": ["...", ...]}
   where each string is a tension-carrying noun phrase.

3. Reduce: one LLM call partitions theme labels into categories with topics string lists.

Usage (from repo root):
  python scripts/LInkedin_Category_Topic_Extraction/discover_topics_from_tiers.py \\
    --output scripts/LInkedin_Category_Topic_Extraction/tiered_posts_grouped/discovered_category_topic_mapping.json

Requires OPENAI_API_KEY; optional OPENAI_BASE_URL. Uses tiktoken for prompt truncation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=True)
except ImportError:
    pass

try:
    import tiktoken
except ImportError:
    tiktoken = None  # type: ignore

from openai import AsyncOpenAI

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

ENCODING_NAME = "cl100k_base"


def _encoding():
    if tiktoken is None:
        raise RuntimeError("tiktoken is required. pip install tiktoken")
    return tiktoken.get_encoding(ENCODING_NAME)


def _iter_user_bundles(raw: Dict[str, Any]) -> Iterable[Tuple[str, Dict[str, Any]]]:
    """Yield (user_id, bundle) for tier-1-shaped JSON.

    Supported shapes:
    - {"results_by_user": { uid: { "posts": [...] }, ... }}
    - {"users": { uid: { "posts": [...] }, ... }, "stats": ...}  (filtered_kept.json)
    - { uid: { "posts": [...] }, ... }  (plain tier-1 export)
    """
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
    skip_keys = {"room_id", "metadata", "results_by_user", "users", "source_file", "stats"}
    for uid, bundle in raw.items():
        if uid in skip_keys:
            continue
        if isinstance(bundle, dict):
            yield str(uid), bundle


def load_tier1_texts(path: Path, category_filter: Optional[str] = None) -> List[str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: List[str] = []
    if not isinstance(raw, dict):
        return out
    cf = category_filter.strip().casefold() if category_filter and category_filter.strip() else None
    for _uid, bundle in _iter_user_bundles(raw):
        for post in bundle.get("posts") or []:
            if not isinstance(post, dict):
                continue
            if cf is not None:
                pc = (post.get("category") or "").strip().casefold()
                if pc != cf:
                    continue
            t = (post.get("text") or post.get("body_text") or "").strip()
            if t:
                out.append(t)
    return out


def load_tier2_texts(path: Path, category_filter: Optional[str] = None) -> List[str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: List[str] = []
    if not isinstance(raw, dict):
        return out
    cf = category_filter.strip().casefold() if category_filter and category_filter.strip() else None
    for _uid, bundle in _iter_user_bundles(raw):
        for row in bundle.get("interactions") or []:
            if not isinstance(row, dict):
                continue
            if cf is not None:
                pc = (row.get("category") or "").strip().casefold()
                if pc != cf:
                    continue
            t = (row.get("response") or row.get("user_text") or "").strip()
            if t:
                out.append(t)
    return out


def truncate_posts_block(texts: List[str], max_tokens: int, reserved_for_template: int) -> str:
    enc = _encoding()
    sep = "\n\n---\n\n"
    budget = max_tokens - reserved_for_template - 500
    if budget < 500:
        budget = 500
    parts: List[str] = []
    used = 0
    for t in texts:
        chunk = t[:8000]
        piece = chunk if not parts else sep + chunk
        nt = len(enc.encode(piece))
        if used + nt > budget:
            break
        parts.append(chunk)
        used += nt
    return sep.join(parts) if parts else ""


def parse_json_object(text: str) -> Dict[str, Any]:
    s = text.strip()
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object in model response")
    return json.loads(s[start: end + 1])


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

ROUND1_SYSTEM = """\
You extract recurring themes from professional LinkedIn posts.

Your goal is to name themes in a way that captures not just the subject matter,
but the human tension, feeling, or stakes that make people care about that subject.

A good theme label is a short noun phrase (4–8 words) that carries the emotional
texture of how this topic lives in a professional's world — not a dry domain label,
not a clickbait headline, not a full sentence.

The label should feel like something a professional would nod at and say
"yes, that's a real thing people struggle with / care about / debate".

GOOD examples (notice: factual enough, but carries weight):
  "hidden cost of moving fast"
  "trust gap between tech and business"
  "burnout disguised as ambition"
  "pressure to have all the answers"
  "seniority without real influence"
  "over-engineered solutions to simple problems"
  "credit that never reaches the right person"

BAD examples:
  "technical debt"                        ← too dry, no tension
  "The silent killer in your codebase"    ← too narrative, headline-style
  "people feel frustrated by process"     ← full sentence, too explicit
  "leadership"                            ← too broad, no texture

Rules:
  - Noun phrase only. 4–8 words. No hashtags.
  - Bake in the tension or feeling — do not state it explicitly.
  - No duplicates or near-duplicates.
  - Return valid JSON only: {"themes": ["...", ...]}
"""


ROUND1_USER = """\
From the posts below, propose exactly {count} distinct theme labels that recur
across this professional LinkedIn content.

Each label must be a 4–8 word noun phrase that carries the human tension or
feeling around the topic — not just the topic name itself.

Posts:
\"\"\"
{posts_block}
\"\"\"

Return JSON: {{"themes": ["...", ...]}} with exactly {count} items unless the
corpus truly supports fewer (minimum 3).\
"""


SUBSEQUENT_SYSTEM = """\
You extend a theme taxonomy for professional LinkedIn posts.

Same rules as before:
  - Each new theme is a 4–8 word noun phrase.
  - It must carry the human tension or feeling around the subject.
  - Do not duplicate or paraphrase any already-discovered theme.
  - Return valid JSON only: {"themes": ["...", ...]} — only NEW themes not
    equivalent to any listed in the prompt.\
"""


SUBSEQUENT_USER = """\
Already discovered themes (do not repeat or paraphrase these):
{previous}

From the NEW posts below, propose up to {max_new} additional themes NOT covered
above. Same rules: 4–8 word noun phrases that carry tension, not dry labels.

Posts:
\"\"\"
{posts_block}
\"\"\"

Return JSON: {{"themes": ["...", ...]}} (empty list allowed if nothing new).\
"""


CATEGORIZE_SYSTEM = """\
You organize a flat list of theme labels into broad categories for an audience taxonomy.

Return valid JSON only:
{"categories": [{"category": "<broad name>", "topics": ["<theme>", ...]}, ...]}

Rules:
  - Obey the min/max category counts given in the user message.
  - Every input theme must appear exactly once in some category's "topics".
  - Category names are 2–5 words, title case, mutually distinct.
  - Do not drop, invent, or rephrase themes — only partition the given list.\
"""


CATEGORIZE_USER = """\
MIN_CATEGORIES = {min_c}
MAX_CATEGORIES = {max_c}

Complete flat list of discovered themes (every string must appear exactly once
under some category — do not drop or rename any):

{themes_json}

Sample post context (for naming categories appropriately):
\"\"\"
{context_snippets}
\"\"\"

Return JSON: {{"categories": [{{"category": "...", "topics": ["...", ...]}}, ...]}}\
"""


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------

async def llm_json(
    client: AsyncOpenAI,
    model: str,
    system: str,
    user: str,
    max_tokens: int,
) -> Dict[str, Any]:
    resp = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
        temperature=0.4,
        max_tokens=max_tokens,
        timeout=120.0,
    )
    content = (resp.choices[0].message.content or "").strip()
    return parse_json_object(content)


def _parse_themes(raw_themes: Any) -> List[str]:
    """Parse LLM themes response into a flat list of strings."""
    out: List[str] = []
    if not isinstance(raw_themes, list):
        return out
    for x in raw_themes:
        if isinstance(x, dict):
            # gracefully handle if model returns objects despite instructions
            s = str(x.get("topic") or x.get("label") or x.get("theme") or "").strip()
        else:
            s = str(x).strip()
        if s:
            out.append(s)
    return out


async def discover_round(
    client: AsyncOpenAI,
    model: str,
    batch: List[str],
    previous: List[str],
    round_num: int,
    initial_count: int,
    subsequent_max: int,
    max_prompt_tokens: int,
) -> List[str]:
    reserved = 1600 if round_num == 1 else 2600
    block = truncate_posts_block(batch, max_prompt_tokens, reserved)
    if not block.strip():
        return []

    if round_num == 1:
        user = ROUND1_USER.format(count=initial_count, posts_block=block)
        system = ROUND1_SYSTEM
    else:
        prev_str = ", ".join(f'"{x}"' for x in previous[:200])
        if len(previous) > 200:
            prev_str += f" ... (+{len(previous) - 200} more)"
        user = SUBSEQUENT_USER.format(
            previous=prev_str,
            max_new=subsequent_max,
            posts_block=block,
        )
        system = SUBSEQUENT_SYSTEM

    data = await llm_json(client, model, system, user, max_tokens=2048)
    raw = data.get("themes")
    parsed = _parse_themes(raw)

    seen: Set[str] = set()
    out: List[str] = []
    for t in parsed:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


async def categorize_all_themes(
    client: AsyncOpenAI,
    model: str,
    themes: List[str],
    pool: List[str],
    min_c: int,
    max_c: int,
    max_prompt_tokens: int,
) -> List[Dict[str, Any]]:
    snippet_block = truncate_posts_block(
        random.sample(pool, min(40, len(pool))), max_prompt_tokens, 4000
    )
    user = CATEGORIZE_USER.format(
        min_c=min_c,
        max_c=max_c,
        themes_json=json.dumps(themes, ensure_ascii=False, indent=2),
        context_snippets=snippet_block,
    )
    data = await llm_json(client, model, CATEGORIZE_SYSTEM, user, max_tokens=8192)
    cats = data.get("categories")
    if not isinstance(cats, list):
        raise ValueError("Expected categories array from categorize step")
    return cats


def validate_partition(
    themes: List[str],
    categories: List[Dict[str, Any]],
) -> Tuple[bool, str]:
    seen: Set[str] = set()
    for row in categories:
        if not isinstance(row, dict):
            return False, "non-dict category row"
        tops = row.get("topics")
        if not isinstance(tops, list):
            return False, "topics not a list"
        for t in tops:
            s = str(t).strip()
            if s in seen:
                return False, f"duplicate topic: {s[:50]}"
            seen.add(s)
    missing = set(themes) - seen
    extra = seen - set(themes)
    if missing:
        return False, f"missing {len(missing)} themes from partition"
    if extra:
        return False, f"extra {len(extra)} themes not in discovery"
    return True, "ok"


# ---------------------------------------------------------------------------
# Main run
# ---------------------------------------------------------------------------

async def run(args: argparse.Namespace) -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        logger.error("OPENAI_API_KEY not set")
        sys.exit(1)

    tier1 = Path(args.tier1).expanduser().resolve()
    out_path = Path(args.output).expanduser().resolve()
    cat_filter: Optional[str] = args.category.strip() if args.category and args.category.strip() else None

    t1 = load_tier1_texts(tier1, category_filter=cat_filter)
    t2: List[str] = []
    if args.tier2:
        tier2 = Path(args.tier2).expanduser().resolve()
        if tier2.is_file():
            t2 = load_tier2_texts(tier2, category_filter=cat_filter)
        else:
            logger.warning("Tier2 path not found, skipping: %s", tier2)

    pool = t1 + t2
    if not pool:
        logger.error("No post texts found. Check tier paths.")
        sys.exit(1)

    if t2:
        logger.info("Loaded %s tier-1 + %s tier-2 = %s texts", len(t1), len(t2), len(pool))
    else:
        logger.info("Loaded %s tier-1 texts (tier-2 off)", len(t1))

    base_url = os.environ.get("OPENAI_BASE_URL")
    client = AsyncOpenAI(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=base_url if base_url and base_url.strip() else None,
        timeout=120.0,
    )

    random.seed(args.seed)
    reviews_left = list(pool)
    all_themes: List[str] = []

    for rnd in range(1, args.max_rounds + 1):
        if not reviews_left:
            logger.info("Round %s: no texts left to sample", rnd)
            break

        k = min(args.sample_size_per_round, len(reviews_left))
        batch = random.sample(reviews_left, k)
        batch_set = set(batch)
        reviews_left = [r for r in reviews_left if r not in batch_set]

        new_themes = await discover_round(
            client=client,
            model=args.model,
            batch=batch,
            previous=all_themes,
            round_num=rnd,
            initial_count=args.initial_themes_count,
            subsequent_max=args.subsequent_rounds_max_themes,
            max_prompt_tokens=args.max_prompt_tokens,
        )

        truly_new = [t for t in new_themes if t not in all_themes]
        logger.info("Round %s: +%s new themes (batch %s texts)", rnd, len(truly_new), k)

        if not truly_new and rnd > 1:
            logger.info("No new themes found; stopping early.")
            break

        all_themes.extend(truly_new)

        if args.test_rounds and rnd >= args.test_rounds:
            logger.info("[TEST] stopping after %s rounds", rnd)
            break

    unique = sorted(set(all_themes))
    logger.info("Unique themes before categorize: %s", len(unique))

    if len(unique) < 3:
        logger.error("Too few themes to categorize (%s). Increase rounds or sample size.", len(unique))
        sys.exit(1)

    categories = await categorize_all_themes(
        client=client,
        model=args.model,
        themes=unique,
        pool=pool,
        min_c=args.min_categories,
        max_c=args.max_categories,
        max_prompt_tokens=args.max_prompt_tokens,
    )

    ok, msg = validate_partition(unique, categories)
    if not ok:
        logger.warning("Partition validation: %s — saving anyway", msg)

    payload: Dict[str, Any] = {
        "categories": categories,
        "metadata": {
            "tier1_file": str(tier1),
            "tier2_file": str(Path(args.tier2).expanduser().resolve()) if args.tier2 else None,
            "category_filter": cat_filter,
            "num_tier1_texts": len(t1),
            "num_tier2_texts": len(t2),
            "num_unique_themes": len(unique),
            "model": args.model,
            "max_rounds": args.max_rounds,
            "sample_size_per_round": args.sample_size_per_round,
            "partition_validation": msg,
        },
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    logger.info("Wrote %s", out_path)


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    default_t1 = root / "scripts/Linkedin_Posts_Extraction/tiered_posts_grouped/tier_1_authored_cleaned.json"
    default_out = root / "scripts/LInkedin_Category_Topic_Extraction/tiered_posts_grouped/discovered_category_topic_mapping.json"

    p = argparse.ArgumentParser(
        description="Map-reduce topic discovery from tier-1 posts → discovered_category_topic_mapping JSON."
    )
    p.add_argument("--tier1", type=str, default=str(default_t1))
    p.add_argument("--tier2", type=str, default=None,
                   help="Optional path to tier-2 interactions JSON.")
    p.add_argument("--category", type=str, default="",
                   help="If set, only posts matching this category label (case-insensitive).")
    p.add_argument("--output", type=str, default=str(default_out))
    p.add_argument("--model", type=str, default=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"))
    p.add_argument("--max-rounds", type=int, default=8)
    p.add_argument("--sample-size-per-round", type=int, default=25)
    p.add_argument("--initial-themes-count", type=int, default=10)
    p.add_argument("--subsequent-rounds-max-themes", type=int, default=5)
    p.add_argument("--min-categories", type=int, default=5)
    p.add_argument("--max-categories", type=int, default=7)
    p.add_argument("--max-prompt-tokens", type=int, default=28000)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--test-rounds", type=int, default=0,
                   help="If >0, stop map phase after this many rounds (for testing).")
    args = p.parse_args()

    asyncio.run(run(args))


if __name__ == "__main__":
    main()