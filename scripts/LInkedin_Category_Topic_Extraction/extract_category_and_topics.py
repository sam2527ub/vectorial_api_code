#!/usr/bin/env python3

"""
Discover topic labels from LinkedIn tier JSON (map-reduce style), then
assign topics into a small set of broad categories — all local I/O, OpenAI API only.

This script is for local/operator use; it is not imported by the application.

Default inputs: tier 1 → ``filtered_kept.json`` (authored posts); tier 2 → ``filtered_kept_tier2.json``.
Use ``--tier 2`` after ``filter_1.py --tier 2``. Theme MAP/SHRINK/CATEGORIZE uses **member reply text**
only (``response`` field), not the original post — that keeps themes anchored in what people wrote.

Pipeline:
  1. MAP   — posts split into at most N balanced groups (default 5); each group is mapped in
     order (first API call = discovery prompt, later calls = extend list). A group can hold
     hundreds or thousands of posts: one request never holds them all. Each group is drained in
     a loop where each API call packs as many posts as fit in the context window, then drops
     those from the queue and repeats until the group is empty (or --max-rounds is hit).
  2. SHRINK — consolidate raw themes: merge near-duplicates, drop redundant ones
  3. CATEGORIZE — partition clean themes into K broad categories

Topics are short noun phrases (4–8 words) that carry the human tension or
feeling around the subject — not dry domain labels, not narrative headlines,
and NOT all starting with "the burden/challenge/struggle of".

Requires OPENAI_API_KEY. Uses tiktoken to size requests to the model context window:
fixed system + user instructions stay intact; post bodies are packed to fill the
remaining input budget (a single oversized post is trimmed by tokens only if needed).
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

# Chat Completions: conservative per-message overhead on top of raw text token counts.
_CHAT_MSG_OVERHEAD = 4

# (model id prefix, max context tokens) — longest match wins after normalization.
_MODEL_CONTEXT_TOKENS: Tuple[Tuple[str, int], ...] = (
    ("gpt-4.1-mini", 1_047_576),
    ("gpt-4.1-nano", 1_047_576),
    ("gpt-4.1", 1_047_576),
    ("gpt-4o-mini", 128_000),
    ("gpt-4o", 128_000),
    ("gpt-4-turbo", 128_000),
    ("gpt-4-32k", 32_768),
    ("gpt-4", 8_192),
    ("gpt-3.5-turbo-16k", 16_385),
    ("gpt-3.5-turbo", 16_385),
    ("o4-mini", 128_000),
    ("o3-mini", 128_000),
    ("o3", 200_000),
    ("o1-mini", 128_000),
    ("o1-preview", 128_000),
    ("o1", 200_000),
)


def _normalize_model_id(model: str) -> str:
    m = (model or "").strip().lower()
    if "/" in m:
        m = m.rsplit("/", 1)[-1]
    return m


def context_window_for_model(model: str, override: Optional[int]) -> int:
    """Total context length (input + output) for budgeting."""
    if override is not None and override > 0:
        return override
    mid = _normalize_model_id(model)
    for prefix, ntok in _MODEL_CONTEXT_TOKENS:
        if mid == prefix or mid.startswith(prefix):
            return ntok
    return 128_000


def _encoding_for_model(model: str):
    if tiktoken is None:
        raise RuntimeError("tiktoken is required. pip install tiktoken")
    slug = _normalize_model_id(model)
    try:
        return tiktoken.encoding_for_model(slug)
    except KeyError:
        return tiktoken.get_encoding(ENCODING_NAME)


def _count_tokens(enc, text: str) -> int:
    return len(enc.encode(text))


def _chat_input_tokens(enc, system: str, user: str) -> int:
    return (
        _CHAT_MSG_OVERHEAD
        + _count_tokens(enc, system)
        + _CHAT_MSG_OVERHEAD
        + _count_tokens(enc, user)
    )


def _trim_text_to_token_budget(enc, text: str, max_tokens: int) -> str:
    if max_tokens <= 0:
        return ""
    if _count_tokens(enc, text) <= max_tokens:
        return text
    lo, hi = 0, len(text)
    best = ""
    while lo <= hi:
        mid = (lo + hi) // 2
        chunk = text[:mid]
        if _count_tokens(enc, chunk) <= max_tokens:
            best = chunk
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def _max_input_tokens(context_window: int, max_output: int, safety_margin: int) -> int:
    return max(1024, context_window - max_output - safety_margin)


def _build_previous_themes_str(previous: List[str], budget_tokens: int, enc) -> str:
    """Longest suffix of `previous` that fits `budget_tokens` (prefers keeping newer themes)."""
    if not previous:
        return ""
    n = len(previous)

    def render(start: int) -> str:
        chunk = previous[start:]
        s = ", ".join(f'"{x}"' for x in chunk)
        if start > 0:
            s += f" ... (+{start} more themes omitted for context)"
        return s

    for start in range(0, n):
        s = render(start)
        if _count_tokens(enc, s) <= budget_tokens:
            return s
    inner = _trim_text_to_token_budget(enc, previous[-1], max(32, budget_tokens - 16))
    return f'"{inner}"' if inner else ""


def _user_fits(enc, system: str, user: str, max_in: int) -> bool:
    return _chat_input_tokens(enc, system, user) <= max_in


def pack_map_prompt(
    enc,
    context_window: int,
    map_completion_reserve: int,
    safety_margin: int,
    round_num: int,
    subsequent_max: int,
    previous: List[str],
    post_queue: List[str],
    is_tier2: bool,
) -> Tuple[str, str, int, int]:
    """
    Build system + user for one map call. Instruction prompts are unchanged; only the
    post block grows until the full message hits the model input budget.

    Returns (system, user, posts_consumed_from_front_of_queue, input_tokens_estimate).
    """
    max_in = _max_input_tokens(context_window, map_completion_reserve, safety_margin)
    r1_sys, r1_usr, sub_sys, sub_usr = (
        (ROUND1_SYSTEM_T2, ROUND1_USER_T2, SUBSEQUENT_SYSTEM_T2, SUBSEQUENT_USER_T2)
        if is_tier2
        else (ROUND1_SYSTEM, ROUND1_USER, SUBSEQUENT_SYSTEM, SUBSEQUENT_USER)
    )
    system = r1_sys if round_num == 1 else sub_sys
    sep = "\n\n---\n\n"

    prev_str = ""
    if round_num > 1:
        prev_budget = max(512, min(24_000, max_in // 3))
        prev_str = _build_previous_themes_str(previous, prev_budget, enc)

    parts: List[str] = []
    for raw in post_queue:
        trial = sep.join(parts + [raw]) if parts else raw
        if round_num == 1:
            user = r1_usr.format(posts_block=trial)
        else:
            user = sub_usr.format(
                previous_str=prev_str,
                max_new=subsequent_max,
                posts_block=trial,
            )
        if _user_fits(enc, system, user, max_in):
            parts.append(raw)
            continue
        if parts:
            break
        # First post alone does not fit — trim that post so the full prompt stays in-budget.
        lo, hi = 0, len(raw)
        best = ""
        while lo <= hi:
            mid = (lo + hi) // 2
            chunk = raw[:mid]
            if round_num == 1:
                u2 = r1_usr.format(posts_block=chunk)
            else:
                u2 = sub_usr.format(
                    previous_str=prev_str,
                    max_new=subsequent_max,
                    posts_block=chunk,
                )
            if _user_fits(enc, system, u2, max_in):
                best = chunk
                lo = mid + 1
            else:
                hi = mid - 1
        if best.strip():
            parts.append(best)
        break

    block = sep.join(parts)
    if round_num == 1:
        user = r1_usr.format(posts_block=block)
    else:
        user = sub_usr.format(
            previous_str=prev_str,
            max_new=subsequent_max,
            posts_block=block,
        )
    inp = _chat_input_tokens(enc, system, user)
    return system, user, len(parts), inp


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _iter_user_bundles(raw: Dict[str, Any]) -> Iterable[Tuple[str, Dict[str, Any]]]:
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


def parse_json_object(text: str) -> Dict[str, Any]:
    s = text.strip()
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object in model response")
    return json.loads(s[start: end + 1])


def split_posts_into_balanced_partitions(
    posts: List[str], max_partitions: int
) -> List[List[str]]:
    """
    Split posts into at most max_partitions contiguous chunks of nearly equal size
    (by post count). Fewer posts than max_partitions yields one chunk per post up to
    the post count.
    """
    if not posts:
        return []
    # At most 5 groups by post count (caller may request fewer).
    k = min(max(1, min(max_partitions, 5)), len(posts))
    if k == 1:
        return [posts[:]]
    q, r = divmod(len(posts), k)
    out: List[List[str]] = []
    i = 0
    for j in range(k):
        sz = q + (1 if j < r else 0)
        out.append(posts[i : i + sz])
        i += sz
    return out


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

ROUND1_SYSTEM = """\
You extract recurring themes from professional LinkedIn posts.

WHAT A GOOD THEME LABEL IS:
A short noun phrase (4–8 words) that carries the human tension, stakes, or
feeling around a subject — not just the subject name itself.

The label should feel like something a professional would immediately recognize
as a real, lived experience in their world. It should be specific enough to be
meaningful, but broad enough that multiple posts could belong to it.

CRITICAL FORMATTING RULES:
- Noun phrase only. 4–8 words. No articles at the start ("the", "a", "an").
  Do NOT start every label with "the burden of" / "the challenge of" /
  "the struggle for" — vary your phrasing completely.
- Bake in the tension naturally — do not use formulaic wrappers.
- No hashtags. No full sentences. No duplicates or near-duplicates.
- Return valid JSON only: {"themes": ["...", ...]}

Quality bar (no example phrases — derive every label from THIS batch of posts):
  - Ground each label in language, roles, and tensions the posts actually use; avoid generic
    "could apply anywhere" slogans or stock professional clichés.
  - Within one response, vary grammatical shape (e.g. contrast, gap/cost metaphor, compound
    stakes, "X without Y", gerund-led hook) so themes do not all read like the same template.
  - Do not echo a single catchy rhythm across the list; if two themes would sound parallel,
    rewrite one into a different structure.

BAD examples — do NOT produce labels like these:
  "technical debt"                          ← too dry, no tension
  "the challenge of technical debt"         ← formulaic wrapper, starts with "the"
  "the burden of unrecognized skills"       ← same problem
  "the struggle for clarity in comms"       ← same problem
  "people feel frustrated by process"       ← full sentence
  "leadership"                              ← too broad, no texture\
"""

ROUND1_USER = """\
Analyze the following professional LinkedIn posts and identify every recurring theme \
or topic that is clearly supported by this batch. There is no target count: include all \
distinct prevalent tensions or through-lines you can justify from the posts; omit weak \
one-offs and do not pad the list.

Consider aspects such as (but not limited to): career growth, technical decisions, \
team dynamics, leadership, AI and tools, hiring, communication, product thinking, \
personal values — but focus on what people ACTUALLY write about in these posts, \
not on a predefined list.

Each theme must be a 4–8 word noun phrase that carries the human tension or feeling \
around the topic. Do NOT start labels with "the burden of / challenge of / struggle for" \
— vary your phrasing. No articles at the start of labels.

Posts:
\"\"\"
{posts_block}
\"\"\"

Return JSON: {{"themes": ["...", ...]}} with no duplicate or near-duplicate strings; \
at least 3 themes when the batch supports that many.\
"""

SUBSEQUENT_SYSTEM = """\
You extend a theme taxonomy for professional LinkedIn posts.

Same rules as the first round:
  - Each new theme is a 4–8 word noun phrase that carries tension or feeling.
  - Do NOT start labels with "the burden of / challenge of / struggle for" — vary phrasing.
  - No articles ("the", "a") at the start of labels.
  - Do not duplicate or paraphrase any already-discovered theme — check semantics, not just wording.
  - Return valid JSON only: {"themes": ["...", ...]} — only NEW themes.\
"""

SUBSEQUENT_USER = """\
Analyze the following professional LinkedIn posts. Identify new themes or topics \
that are NOT already in this list: {previous_str}

Cap the number of new themes at {max_new}. If no genuinely new themes are found, \
return an empty array.

Same rules: 4–8 word noun phrases, vary phrasing structure, no "the X of Y" templates, \
no semantic duplicates of the existing list.

Posts:
\"\"\"
{posts_block}
\"\"\"

Return JSON: {{"themes": ["...", ...]}} (empty list if nothing new).\
"""

# --- Tier-2 (member replies / interaction text): same JSON shape, wording tuned for comments ---
ROUND1_SYSTEM_T2 = """\
You extract recurring themes from **LinkedIn member interaction text** (comments, replies, \
and short notes on reshares — the member's own words, not the original thread alone).

WHAT A GOOD THEME LABEL IS:
A short noun phrase (10-15 words) that carries the human tension, stakes, or
feeling around a subject — not just the subject name itself.

The label should feel like something a professional would immediately recognize
as a real, lived experience in their world. It should be specific enough to be
meaningful, but broad enough that multiple replies could belong to it.

CRITICAL FORMATTING RULES:
- Noun phrase only. 4-8 words. No articles at the start ("the", "a", "an").
  Do NOT start every label with "the burden of" / "the challenge of" /
  "the struggle for" — vary your phrasing completely.
- Bake in the tension naturally — do not use formulaic wrappers.
- No hashtags. No full sentences. No duplicates or near-duplicates.
- Return valid JSON only: {"themes": ["...", ...]}

Quality bar (no example phrases — derive every label from THIS batch of replies):
  - Ground each label in language, roles, and tensions the replies actually use; avoid generic
    "could apply anywhere" slogans or stock professional clichés.
  - Within one response, vary grammatical shape so themes do not all read like the same template.
  - Do not echo a single catchy rhythm across the list; if two themes would sound parallel,
    rewrite one into a different structure.

BAD examples — do NOT produce labels like these:
  "technical debt"                          ← too dry, no tension
  "the challenge of technical debt"         ← formulaic wrapper, starts with "the"
  "the burden of unrecognized skills"       ← same problem
  "the struggle for clarity in comms"       ← same problem
  "people feel frustrated by process"       ← full sentence
  "leadership"                              ← too broad, no texture\
"""

ROUND1_USER_T2 = """\
Analyze the following **member replies** (LinkedIn interaction text) and identify every recurring theme \
or topic that is clearly supported by this batch. There is no target count: include all \
distinct prevalent tensions or through-lines you can justify from the replies; omit weak \
one-offs and do not pad the list.

Focus on what people ACTUALLY express in these replies — agreement, pushback, identity, \
career subtext, tooling, leadership, AI, hiring, communication, product — not a predefined list.

Each theme must be a 4-8 word noun phrase that carries the human tension or feeling \
around the topic. Do NOT start labels with "the burden of / challenge of / struggle for" \
— vary your phrasing. No articles at the start of labels.

Replies (one block per item, separated by ---):
\"\"\"
{posts_block}
\"\"\"

Return JSON: {{"themes": ["...", ...]}} with no duplicate or near-duplicate strings; \
at least 3 themes when the batch supports that many.\
"""

SUBSEQUENT_SYSTEM_T2 = """\
You extend a theme taxonomy for **LinkedIn member interaction text** (replies and short notes).

Same rules as the first round:
  - Each new theme is a 4-8 word noun phrase that carries tension or feeling.
  - Do NOT start labels with "the burden of / challenge of / struggle for" — vary phrasing.
  - No articles ("the", "a") at the start of labels.
  - Do not duplicate or paraphrase any already-discovered theme — check semantics, not just wording.
  - Return valid JSON only: {"themes": ["...", ...]} — only NEW themes.\
"""

SUBSEQUENT_USER_T2 = """\
Analyze the following member replies. Identify new themes or topics \
that are NOT already in this list: {previous_str}

Cap the number of new themes at {max_new}. If no genuinely new themes are found, \
return an empty array.

Same rules: 4–8 word noun phrases, vary phrasing structure, no "the X of Y" templates, \
no semantic duplicates of the existing list.

Replies:
\"\"\"
{posts_block}
\"\"\"

Return JSON: {{"themes": ["...", ...]}} (empty list if nothing new).\
"""

CATEGORIZE_USER_T2 = """\
MIN_CATEGORIES = {min_c}
MAX_CATEGORIES = {max_c}

Organize ALL of the following themes into {min_c}–{max_c} broad categories.
Every theme must appear exactly once. Do not drop any theme. Do not rename any theme string.

Themes to partition ({count} total):
{themes_json}

Sample **member reply** snippets (use to understand the audience when naming categories):
\"\"\"
{context_snippets}
\"\"\"

Return JSON: {{"categories": [{{"category": "...", "topics": ["...", ...]}}, ...]}}\
"""

SHRINK_SYSTEM = """\
You consolidate a raw list of theme labels extracted from professional LinkedIn posts.

Your job:
  - Merge themes that are semantically equivalent or near-duplicate (same idea, different words).
  - Eliminate redundant or overly specific themes that are sub-cases of a broader one.
  - Keep themes that represent genuinely distinct professional experiences or tensions.
  - Produce a final clean list of 10–25 distinct themes.

Output rules for the final labels:
  - Each label is a 4–8 word noun phrase.
  - NO labels starting with "the burden of / challenge of / struggle for" — vary phrasing.
  - No articles ("the", "a", "an") at the start.
  - Bake in the tension naturally without formulaic wrappers.
  - The label should feel like a real, lived professional experience.
  - Return valid JSON only: {"themes": ["...", ...]}\
"""

SHRINK_USER = """\
Consolidate the following list of themes extracted from professional LinkedIn posts.

Merge similar themes, eliminate near-duplicates, and remove redundant ones to produce \
a final concise list of 10 to 25 distinct themes.

The final themes should:
  - Represent the most important and frequently occurring professional experiences
  - Be high-level enough to group multiple posts under them
  - Each be a 4–8 word noun phrase with tension baked in — no "the X of Y" templates
  - Cover genuinely different territory from each other

Raw themes to consolidate:
{themes_json}

Return JSON: {{"themes": ["...", ...]}} with 10–25 items.\
"""

CATEGORIZE_SYSTEM = """\
You organize a flat list of theme labels into broad categories for an audience taxonomy.

Return valid JSON only:
{"categories": [{"category": "<broad name>", "topics": ["<theme>", ...]}, ...]}

Rules:
  - Obey the min/max category counts given in the user message.
  - Every input theme must appear exactly once across all categories — do not drop any, do not add any.
  - Category names are 2–5 words, title case, mutually distinct.
  - Do not rename, rephrase, or modify any theme string — copy them exactly.
  - Groups within a category should feel genuinely related, not just loosely connected.\
"""

CATEGORIZE_USER = """\
MIN_CATEGORIES = {min_c}
MAX_CATEGORIES = {max_c}

Organize ALL of the following themes into {min_c}–{max_c} broad categories.
Every theme must appear exactly once. Do not drop any theme. Do not rename any theme string.

Themes to partition ({count} total):
{themes_json}

Sample post context (use this to understand the audience when naming categories):
\"\"\"
{context_snippets}
\"\"\"

Return JSON: {{"categories": [{{"category": "...", "topics": ["...", ...]}}, ...]}}\
"""


def pack_categorize_prompt(
    enc,
    context_window: int,
    categorize_max_output: int,
    safety_margin: int,
    min_c: int,
    max_c: int,
    themes: List[str],
    pool: List[str],
    rng: random.Random,
    is_tier2: bool,
) -> Tuple[str, str, int]:
    """
    Fixed categorize instructions + full themes_json; pack as many shuffled post snippets
    as fit under the model input budget (same counting rules as map).
    """
    max_in = _max_input_tokens(context_window, categorize_max_output, safety_margin)
    system = CATEGORIZE_SYSTEM
    themes_json = json.dumps(themes, ensure_ascii=False, indent=2)
    cat_user_tpl = CATEGORIZE_USER_T2 if is_tier2 else CATEGORIZE_USER
    shuffled = pool[:]
    rng.shuffle(shuffled)
    sep = "\n\n---\n\n"
    parts: List[str] = []
    for raw in shuffled:
        trial = sep.join(parts + [raw]) if parts else raw
        user = cat_user_tpl.format(
            min_c=min_c,
            max_c=max_c,
            count=len(themes),
            themes_json=themes_json,
            context_snippets=trial,
        )
        if _user_fits(enc, system, user, max_in):
            parts.append(raw)
            continue
        if not parts:
            lo, hi = 0, len(raw)
            best = ""
            while lo <= hi:
                mid = (lo + hi) // 2
                chunk = raw[:mid]
                u2 = cat_user_tpl.format(
                    min_c=min_c,
                    max_c=max_c,
                    count=len(themes),
                    themes_json=themes_json,
                    context_snippets=chunk,
                )
                if _user_fits(enc, system, u2, max_in):
                    best = chunk
                    lo = mid + 1
                else:
                    hi = mid - 1
            if best.strip():
                parts.append(best)
        break
    block = sep.join(parts)
    user = cat_user_tpl.format(
        min_c=min_c,
        max_c=max_c,
        count=len(themes),
        themes_json=themes_json,
        context_snippets=block,
    )
    inp = _chat_input_tokens(enc, system, user)
    return system, user, inp


# ---------------------------------------------------------------------------
# LLM helper
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
    out: List[str] = []
    if not isinstance(raw_themes, list):
        return out
    for x in raw_themes:
        if isinstance(x, dict):
            s = str(x.get("topic") or x.get("label") or x.get("theme") or "").strip()
        else:
            s = str(x).strip()
        if s:
            out.append(s)
    return out


# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------

async def map_round(
    client: AsyncOpenAI,
    model: str,
    enc,
    context_window: int,
    safety_margin: int,
    post_queue: List[str],
    previous: List[str],
    round_num: int,
    subsequent_max: int,
    map_max_output: int,
    is_tier2: bool,
) -> Tuple[List[str], int]:
    """
    Pack as many posts from the front of post_queue as fit under the model input budget
    (full system + user instructions, counted with tiktoken). Returns (themes, num_posts_consumed).

    Callers with more posts than fit in one window should invoke this repeatedly on the same
    queue, advancing with del queue[:consumed], until the queue is empty.

    Round 1 asks for all well-supported themes (no fixed N); completion budget is at least
    8192 tokens so the JSON list can grow. Later rounds still cap new themes via subsequent_max.
    """
    if not post_queue:
        return [], 0
    # First round: no fixed theme-count cap in the prompt; reserve more completion tokens
    # for a longer themes array. Later rounds keep map_max_output. Cap completion at
    # max(map_max_output, context_window // 2) so small-context models still get input room.
    completion_reserve = max(map_max_output, 8192) if round_num == 1 else map_max_output
    completion_reserve = min(
        completion_reserve,
        max(map_max_output, context_window // 2),
    )
    system, user, n, inp = pack_map_prompt(
        enc,
        context_window,
        completion_reserve,
        safety_margin,
        round_num,
        subsequent_max,
        previous,
        post_queue,
        is_tier2,
    )
    budget = _max_input_tokens(context_window, completion_reserve, safety_margin)
    if n == 0:
        logger.error(
            "Map round %s: no post text fits in context (window=%s, input_budget≈%s, est_with_shell=%s)",
            round_num,
            context_window,
            budget,
            inp,
        )
        return [], 0
    logger.info(
        "Map round %s: packed %s post(s), est. chat input tokens %s (budget %s)",
        round_num,
        n,
        inp,
        budget,
    )
    data = await llm_json(client, model, system, user, max_tokens=completion_reserve)
    parsed = _parse_themes(data.get("themes"))

    seen: Set[str] = set()
    out: List[str] = []
    for t in parsed:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out, n


async def shrink_themes(
    client: AsyncOpenAI,
    model: str,
    enc,
    context_window: int,
    safety_margin: int,
    raw_themes: List[str],
    depth: int = 0,
) -> List[str]:
    """Consolidate raw map output: merge near-duplicates, drop redundant ones."""
    if depth > 14:
        logger.warning("Shrink: max recursion depth — returning deduped slice of raw themes")
        return sorted(set(raw_themes))[:40]

    max_in = _max_input_tokens(context_window, 4096, safety_margin)

    async def one_call(themes_list: List[str], indent: Optional[int]) -> Optional[List[str]]:
        tj = json.dumps(themes_list, ensure_ascii=False, indent=indent)
        user = SHRINK_USER.format(themes_json=tj)
        if not _user_fits(enc, SHRINK_SYSTEM, user, max_in):
            return None
        data = await llm_json(client, model, SHRINK_SYSTEM, user, max_tokens=4096)
        return _parse_themes(data.get("themes"))

    for indent in (2, None):
        consolidated = await one_call(raw_themes, indent)
        if consolidated is None:
            continue
        if not consolidated:
            logger.warning("Shrink returned empty list — falling back to raw themes")
            return sorted(set(raw_themes))
        logger.info(
            "Shrink: %s raw → %s consolidated themes (json_indent=%s)",
            len(raw_themes),
            len(consolidated),
            indent,
        )
        return consolidated

    n = len(raw_themes)
    if n <= 1:
        logger.warning("Shrink: could not fit themes in context — falling back to raw list")
        return sorted(set(raw_themes))
    mid = n // 2
    left = await shrink_themes(
        client, model, enc, context_window, safety_margin, raw_themes[:mid], depth + 1
    )
    right = await shrink_themes(
        client, model, enc, context_window, safety_margin, raw_themes[mid:], depth + 1
    )
    merged = left + right
    logger.info("Shrink: split-merge intermediate list (%s themes), re-shrinking", len(merged))
    return await shrink_themes(
        client, model, enc, context_window, safety_margin, merged, depth + 1
    )

async def categorize_themes(
    client: AsyncOpenAI,
    model: str,
    enc,
    context_window: int,
    safety_margin: int,
    themes: List[str],
    pool: List[str],
    min_c: int,
    max_c: int,
    rng: random.Random,
    is_tier2: bool,
) -> List[Dict[str, Any]]:
    system, user, inp = pack_categorize_prompt(
        enc,
        context_window,
        8192,
        safety_margin,
        min_c,
        max_c,
        themes,
        pool,
        rng,
        is_tier2,
    )
    logger.info(
        "Categorize: est. chat input tokens %s (budget %s)",
        inp,
        _max_input_tokens(context_window, 8192, safety_margin),
    )
    data = await llm_json(client, model, system, user, max_tokens=8192)
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

    is_tier2: bool = args.tier == "2"
    input_path = Path(args.tier1).expanduser().resolve()
    out_path = Path(args.output).expanduser().resolve()
    cat_filter: Optional[str] = (
        args.category.strip() if args.category and args.category.strip() else None
    )

    t1: List[str] = []
    t2_aux: List[str] = []

    if is_tier2:
        if args.tier2:
            logger.warning("--tier2 is ignored when --tier 2 (use --tier1 for the tier-2 JSON path).")
        pool = load_tier2_texts(input_path, category_filter=cat_filter)
        if not pool:
            logger.error("No tier-2 interaction texts found. Check --tier1 (e.g. filtered_kept_tier2.json).")
            sys.exit(1)
        logger.info("Tier-2 mode: loaded %s interaction texts from %s", len(pool), input_path)
    else:
        t1 = load_tier1_texts(input_path, category_filter=cat_filter)
        if args.tier2:
            tier2_path = Path(args.tier2).expanduser().resolve()
            if tier2_path.is_file():
                t2_aux = load_tier2_texts(tier2_path, category_filter=cat_filter)
            else:
                logger.warning("Tier2 path not found, skipping: %s", tier2_path)
        pool = t1 + t2_aux
        if not pool:
            logger.error("No post texts found. Check tier paths.")
            sys.exit(1)
        logger.info(
            "Loaded %s tier-1 + %s tier-2 (aux) = %s texts total",
            len(t1),
            len(t2_aux),
            len(pool),
        )

    base_url = os.environ.get("OPENAI_BASE_URL")
    client = AsyncOpenAI(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=base_url if base_url and base_url.strip() else None,
        timeout=300.0,
    )

    enc = _encoding_for_model(args.model)
    ctx_override = args.context_tokens if args.context_tokens > 0 else None
    context_window = context_window_for_model(args.model, ctx_override)
    if args.max_prompt_tokens > 0:
        context_window = min(context_window, args.max_prompt_tokens)

    random.seed(args.seed)
    rng_cat = random.Random(args.seed ^ 0x9E3779B9)
    reviews_left = list(pool)
    random.shuffle(reviews_left)
    all_raw_themes: List[str] = []

    logger.info(
        "Context packing: model=%s, window=%s tokens, safety_margin=%s, map_max_output=%s",
        args.model,
        context_window,
        args.safety_margin,
        args.map_max_output,
    )

    # ── STEP 1: MAP ──────────────────────────────────────────────────────────
    logger.info("=== STEP 1: MAP ===")
    partitions = split_posts_into_balanced_partitions(
        reviews_left, args.map_post_partitions
    )
    logger.info(
        "MAP: %s texts → %s partition(s) (max %s by count)",
        len(reviews_left),
        len(partitions),
        args.map_post_partitions,
    )

    map_api_calls = 0
    stop_map_early = False
    for part_i, partition in enumerate(partitions):
        queue = partition[:]
        logger.info(
            "MAP partition %s/%s: %s text(s) — drained with repeated API calls until empty "
            "(each call packs only what fits in the model context)",
            part_i + 1,
            len(partitions),
            len(queue),
        )
        while queue:
            if map_api_calls >= args.max_rounds:
                skipped_posts = len(queue) + sum(
                    len(partitions[j]) for j in range(part_i + 1, len(partitions))
                )
                logger.warning(
                    "MAP: hit --max-rounds (%s) API-call cap; ~%s post(s) not processed "
                    "(raise --max-rounds; large corpora need many context-sized batches)",
                    args.max_rounds,
                    skipped_posts,
                )
                stop_map_early = True
                break

            map_api_calls += 1
            # First map call ever uses ROUND1; all later calls use SUBSEQUENT + prior themes.
            round_num = 1 if map_api_calls == 1 else 2

            new_themes, consumed = await map_round(
                client=client,
                model=args.model,
                enc=enc,
                context_window=context_window,
                safety_margin=args.safety_margin,
                post_queue=queue,
                previous=all_raw_themes,
                round_num=round_num,
                subsequent_max=args.subsequent_rounds_max_themes,
                map_max_output=args.map_max_output,
                is_tier2=is_tier2,
            )
            if consumed == 0:
                logger.error("Map produced no consumable texts; aborting.")
                sys.exit(1)
            del queue[:consumed]

            truly_new = [t for t in new_themes if t not in all_raw_themes]
            logger.info(
                "MAP api_call=%s partition=%s/%s: +%s new themes, consumed %s text(s), %s left in partition",
                map_api_calls,
                part_i + 1,
                len(partitions),
                len(truly_new),
                consumed,
                len(queue),
            )

            if not truly_new and map_api_calls > 1:
                logger.info("No new themes — stopping map early.")
                stop_map_early = True
                break

            all_raw_themes.extend(truly_new)

            if args.test_rounds and map_api_calls >= args.test_rounds:
                logger.info("[TEST] stopping after %s map API calls", map_api_calls)
                stop_map_early = True
                break

        if stop_map_early:
            break

    logger.info(
        "MAP complete: %s raw themes (%s map API calls)",
        len(all_raw_themes),
        map_api_calls,
    )

    if len(all_raw_themes) < 3:
        logger.error(
            "Too few raw themes (%s). Check API responses or raise --max-rounds / partitions.",
            len(all_raw_themes),
        )
        sys.exit(1)

    # ── STEP 2: SHRINK ───────────────────────────────────────────────────────
    logger.info("=== STEP 2: SHRINK ===")
    clean_themes = await shrink_themes(
        client,
        args.model,
        enc,
        context_window,
        args.safety_margin,
        all_raw_themes,
    )

    if len(clean_themes) < 3:
        logger.error("Too few themes after shrink (%s).", len(clean_themes))
        sys.exit(1)

    # ── STEP 3: CATEGORIZE ───────────────────────────────────────────────────
    logger.info("=== STEP 3: CATEGORIZE ===")
    categories = await categorize_themes(
        client=client,
        model=args.model,
        enc=enc,
        context_window=context_window,
        safety_margin=args.safety_margin,
        themes=clean_themes,
        pool=pool,
        min_c=args.min_categories,
        max_c=args.max_categories,
        rng=rng_cat,
        is_tier2=is_tier2,
    )

    ok, msg = validate_partition(clean_themes, categories)
    if not ok:
        logger.warning("Partition validation: %s — saving anyway", msg)
    else:
        logger.info("Partition validation: ok")

    # ── SAVE ─────────────────────────────────────────────────────────────────
    payload: Dict[str, Any] = {
        "categories": categories,
        "metadata": {
            "pipeline_tier": "2" if is_tier2 else "1",
            "primary_input": str(input_path),
            "tier1_file": str(input_path),
            "tier2_aux_file": (
                str(Path(args.tier2).expanduser().resolve()) if (not is_tier2 and args.tier2) else None
            ),
            "category_filter": cat_filter,
            "num_tier1_texts": 0 if is_tier2 else len(t1),
            "num_tier2_texts": len(pool) if is_tier2 else len(t2_aux),
            "num_raw_themes": len(all_raw_themes),
            "num_clean_themes": len(clean_themes),
            "model": args.model,
            "context_window_tokens": context_window,
            "safety_margin": args.safety_margin,
            "map_max_output": args.map_max_output,
            "map_post_partitions": args.map_post_partitions,
            "map_api_calls": map_api_calls,
            "max_rounds": args.max_rounds,
            "sample_size_per_round": args.sample_size_per_round,
            "partition_validation": msg,
        },
        "raw_themes": all_raw_themes,
        "clean_themes": clean_themes,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    logger.info("Wrote %s", out_path)


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    extraction_dir = root / "scripts/LInkedin_Category_Topic_Extraction/tiered_posts_grouped"
    default_tier1_in = extraction_dir / "filtered_kept.json"
    default_tier2_in = extraction_dir / "filtered_kept_tier2.json"
    default_tier1_out = extraction_dir / "discovered_category_topic_mapping.json"
    default_tier2_out = extraction_dir / "discovered_category_topic_mapping_tier2.json"

    p = argparse.ArgumentParser(
        description="Map → Shrink → Categorize topic discovery from LinkedIn authored posts or tier-2 replies."
    )
    p.add_argument(
        "--tier",
        choices=("1", "2"),
        default="1",
        help="1 = tier-1 posts JSON; 2 = tier-2 interactions JSON (response text only in map).",
    )
    p.add_argument(
        "--tier1",
        type=str,
        default=None,
        help="Primary input JSON (--tier 1: posts; --tier 2: interactions). Defaults: filtered_kept*.json.",
    )
    p.add_argument(
        "--tier2",
        type=str,
        default=None,
        help="Optional second JSON: extra tier-2 texts when --tier 1. Ignored when --tier 2.",
    )
    p.add_argument("--category", type=str, default="")
    p.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output JSON path. Default: discovered_category_topic_mapping.json or _tier2 variant.",
    )
    p.add_argument("--model", type=str, default=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"))
    p.add_argument(
        "--context-tokens",
        type=int,
        default=0,
        help="Override total model context length for packing (0 = infer from --model).",
    )
    p.add_argument(
        "--max-prompt-tokens",
        type=int,
        default=0,
        help="If >0, cap context at min(model, this). 0 = use full model context from table.",
    )
    p.add_argument(
        "--safety-margin",
        type=int,
        default=2048,
        help="Tokens reserved below (context − completion) when packing inputs.",
    )
    p.add_argument(
        "--map-max-output",
        type=int,
        default=2048,
        help="max_tokens for map-phase JSON responses (subtracted from context when packing).",
    )
    p.add_argument(
        "--map-post-partitions",
        type=int,
        default=5,
        help="Split shuffled posts into at most this many equal-count groups (hard cap 5, capped at post count); each group is mapped in order.",
    )
    p.add_argument(
        "--max-rounds",
        type=int,
        default=512,
        help=(
            "Safety cap on total map API calls. Large partitions need many batches "
            "(e.g. 2k posts ≫ one context); increase this so every post can be seen."
        ),
    )
    p.add_argument(
        "--sample-size-per-round",
        type=int,
        default=25,
        help="Deprecated: posts are packed to the model context automatically; value is unused.",
    )
    p.add_argument(
        "--initial-themes-count",
        type=int,
        default=10,
        help="Deprecated: ignored. The first map round no longer uses a fixed theme count.",
    )
    p.add_argument("--subsequent-rounds-max-themes", type=int, default=5)
    p.add_argument("--min-categories", type=int, default=5)
    p.add_argument("--max-categories", type=int, default=7)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--test-rounds", type=int, default=0,
                   help="Stop map phase after this many rounds (for testing).")
    args = p.parse_args()

    if args.tier1 is None:
        args.tier1 = str(default_tier2_in if args.tier == "2" else default_tier1_in)
    if args.output is None:
        args.output = str(default_tier2_out if args.tier == "2" else default_tier1_out)

    asyncio.run(run(args))


if __name__ == "__main__":
    main()
