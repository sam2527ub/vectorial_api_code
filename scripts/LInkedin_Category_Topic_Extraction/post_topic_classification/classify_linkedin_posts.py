#!/usr/bin/env python3
"""
Two-stage LinkedIn post classification (category → topics) with optional logprobs.

Stage 1: Batched LLM calls assign each post to exactly one taxonomy category
        (from ``discovered_category_topic_mapping.json``)—the single most relevant label from the allowed set.
Stage 2: Per-post LLM calls pick topics only from that category's topic list;
        requests logprobs when the model supports them (same pattern as review pipeline).

Input: tiered JSON from Linkedin_Posts_Extraction (dict keyed by profile_id).
Output: JSONL lines, one per post (resume-safe by post_id).

Usage (from repo root):
  python scripts/LInkedin_Category_Topic_Extraction/post_topic_classification/classify_linkedin_posts.py \\
    --input scripts/Linkedin_Posts_Extraction/tiered_posts_grouped/tier_1_authored.json \\
    --mapping scripts/LInkedin_Category_Topic_Extraction/tiered_posts_grouped/discovered_category_topic_mapping.json \\
    --output scripts/LInkedin_Category_Topic_Extraction/tiered_posts_grouped/post_topic_labels.jsonl

Requires OPENAI_API_KEY; optional OPENAI_BASE_URL (e.g. Bedrock-compatible gateway).
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import logging
import math
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[3] / ".env", override=True)
except ImportError:
    pass

from openai import AsyncOpenAI

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"


def extract_token_probabilities_from_response(logprobs_data: Any, predicted_topics: List[str]) -> Dict[str, float]:
    """Map each predicted topic to an approximate token-level confidence (same idea as review stage)."""
    if not logprobs_data or not hasattr(logprobs_data, "content"):
        return {}

    token_probs: Dict[str, List[float]] = defaultdict(list)
    target_map = {t.lower().strip(): t for t in predicted_topics}

    try:
        for token_info in logprobs_data.content:
            token_text = token_info.token.lower().strip()
            if token_text in ['"', "{", "}", "[", "]", ":", ","]:
                continue
            current_prob = math.exp(token_info.logprob)
            for target_lower, original_name in target_map.items():
                if token_text in target_lower or target_lower in token_text:
                    token_probs[original_name].append(current_prob)
    except Exception as e:
        logger.error("Error calculating token probabilities: %s", e)
        return {}

    return {topic: sum(probs) / len(probs) for topic, probs in token_probs.items() if probs}


def model_supports_logprobs(model_name: str) -> bool:
    m = model_name.lower()
    return any(x in m for x in ("gpt-4", "gpt-3.5", "gpt-4o", "gpt-4-turbo", "gpt-oss", "oss"))


def load_prompt(name: str) -> str:
    path = PROMPT_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Missing prompt: {path}")
    return path.read_text(encoding="utf-8").strip()


def load_category_mapping(path: Path) -> Tuple[List[str], Dict[str, List[str]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    categories: List[str] = []
    topics_by_category: Dict[str, List[str]] = {}
    for row in data.get("categories", []):
        cat = row.get("category")
        topics = row.get("topics") or []
        if not cat:
            continue
        categories.append(cat)
        topics_by_category[cat] = list(topics)
    if not categories:
        raise ValueError(f"No categories in {path}")
    return categories, topics_by_category


def load_tiered_posts(path: Path) -> List[Dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: List[Dict[str, Any]] = []
    if not isinstance(raw, dict):
        raise ValueError("Expected tier JSON to be an object keyed by profile_id")
    for profile_id, bundle in raw.items():
        if not isinstance(bundle, dict):
            continue
        profile_info = bundle.get("profile_info") or {}
        occupation = profile_info.get("occupation")
        for post in bundle.get("posts") or []:
            if not isinstance(post, dict):
                continue
            text = (post.get("text") or "").strip()
            pid = post.get("post_id")
            if not pid or not text:
                continue
            out.append({
                "post_id": str(pid),
                "profile_id": str(profile_id),
                "occupation": occupation,
                "text": text,
            })
    return out


def normalize_category(raw: Optional[str], allowed: List[str]) -> Optional[str]:
    """Map model output to exactly one allowed category string, or None."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if s in allowed:
        return s
    lower_map = {a.lower(): a for a in allowed}
    if s.lower() in lower_map:
        return lower_map[s.lower()]
    # Near-miss (e.g. minor typo); still a single label from the set
    matches = difflib.get_close_matches(s, allowed, n=1, cutoff=0.72)
    if matches:
        return matches[0]
    return None


def parse_json_object(text: str) -> Dict[str, Any]:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object in model response")
    return json.loads(text[start : end + 1])


def get_processed_ids(output_path: Path) -> set:
    done = set()
    if not output_path.exists():
        return done
    with output_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                if row.get("post_id"):
                    done.add(row["post_id"])
            except json.JSONDecodeError:
                continue
    return done


async def classify_category_batch(
    client: AsyncOpenAI,
    model: str,
    posts_slice: List[Dict[str, Any]],
    allowed_categories: List[str],
    max_text_chars: int,
    max_retries: int,
    retry_delay: float,
) -> List[Dict[str, Any]]:
    """One LLM call for many posts → results aligned to input order."""
    system_t = load_prompt("category_batch_system.txt")
    user_t = load_prompt("category_batch_user.txt")
    allowed_json = json.dumps(allowed_categories, ensure_ascii=False, indent=2)
    posts_payload = [
        {"post_id": p["post_id"], "text": p["text"][:max_text_chars]}
        for p in posts_slice
    ]
    posts_json = json.dumps(posts_payload, ensure_ascii=False, indent=2)
    system_message = system_t.format(allowed_categories_json=allowed_json)
    user_message = user_t.format(posts_json=posts_json)

    last_err: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            resp = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": user_message},
                ],
                response_format={"type": "json_object"},
                timeout=120.0,
            )
            content = (resp.choices[0].message.content or "").strip()
            data = parse_json_object(content)
            results = data.get("results")
            if not isinstance(results, list) or len(results) != len(posts_slice):
                raise ValueError(
                    f"Expected results len {len(posts_slice)}, got {len(results) if isinstance(results, list) else type(results)}"
                )
            normalized: List[Dict[str, Any]] = []
            for i, row in enumerate(results):
                pid = row.get("post_id") or posts_slice[i]["post_id"]
                cat = normalize_category(row.get("category"), allowed_categories)
                conf = row.get("category_confidence")
                try:
                    conf_f = float(conf) if conf is not None else None
                except (TypeError, ValueError):
                    conf_f = None
                if cat is None:
                    logger.warning(
                        "Could not map category for post_id=%s raw=%r; allowed labels: %s",
                        pid,
                        row.get("category"),
                        allowed_categories,
                    )
                normalized.append({
                    "post_id": pid,
                    "category": cat,
                    "category_confidence": conf_f,
                    "brief_rationale": row.get("brief_rationale"),
                })
            return normalized
        except Exception as e:
            last_err = e
            logger.warning("Category batch attempt %s failed: %s", attempt + 1, e)
            await asyncio.sleep(retry_delay * (attempt + 1))
    raise RuntimeError(f"Category batch failed after retries: {last_err}")


async def extract_topics_for_post(
    client: AsyncOpenAI,
    model: str,
    post_id: str,
    text: str,
    topic_universe: List[str],
    semaphore: asyncio.Semaphore,
    max_retries: int,
    retry_delay: float,
    max_text_chars: int,
) -> Dict[str, Any]:
    if not topic_universe:
        return {
            "post_id": post_id,
            "error": "empty_topic_universe",
            "identified_themes": [],
            "theme_token_probabilities": {},
            "sentiment": None,
        }

    user_prompt = load_prompt("post_topic_user.txt").format(
        post_text=text[:max_text_chars],
        topic_universe=json.dumps(topic_universe, ensure_ascii=False, indent=2),
    )
    system_prompt = load_prompt("post_topic_system.txt")

    async with semaphore:
        for attempt in range(max_retries):
            try:
                api_params: Dict[str, Any] = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "response_format": {"type": "json_object"},
                    "timeout": 90.0,
                }
                if model_supports_logprobs(model):
                    api_params["logprobs"] = True
                    api_params["top_logprobs"] = 3

                completion = await client.chat.completions.create(**api_params)
                response_content = (completion.choices[0].message.content or "").strip()
                response_json = parse_json_object(response_content)

                identified = response_json.get("identified_themes", [])
                if not isinstance(identified, list):
                    identified = []
                identified = [str(x).strip() for x in identified if str(x).strip()]
                # Keep only topics that exist in universe (exact match, then case-insensitive)
                universe_set = set(topic_universe)
                uni_lower = {t.lower(): t for t in topic_universe}
                cleaned: List[str] = []
                for t in identified:
                    if t in universe_set:
                        cleaned.append(t)
                    elif t.lower() in uni_lower:
                        cleaned.append(uni_lower[t.lower()])
                identified = list(dict.fromkeys(cleaned))

                sentiment = response_json.get("sentiment")
                json_topic_probs = response_json.get("topic_probabilities") or {}
                if not isinstance(json_topic_probs, dict):
                    json_topic_probs = {}

                real_token_probs: Dict[str, float] = {}
                ch0 = completion.choices[0]
                if hasattr(ch0, "logprobs") and ch0.logprobs:
                    real_token_probs = extract_token_probabilities_from_response(ch0.logprobs, identified)

                theme_token_probabilities: Dict[str, float] = {}
                for theme in identified:
                    if theme in real_token_probs:
                        theme_token_probabilities[theme] = real_token_probs[theme]
                    elif theme in json_topic_probs:
                        try:
                            theme_token_probabilities[theme] = float(json_topic_probs[theme])
                        except (TypeError, ValueError):
                            theme_token_probabilities[theme] = 0.5
                    else:
                        theme_token_probabilities[theme] = 0.5

                return {
                    "post_id": post_id,
                    "identified_themes": identified,
                    "theme_token_probabilities": theme_token_probabilities,
                    "sentiment": sentiment,
                    "error": None,
                }
            except Exception as e:
                logger.warning("Topic extract attempt %s for %s: %s", attempt + 1, post_id, e)
                if attempt == max_retries - 1:
                    return {
                        "post_id": post_id,
                        "error": str(e),
                        "identified_themes": [],
                        "theme_token_probabilities": {},
                        "sentiment": None,
                    }
                await asyncio.sleep(retry_delay * (attempt + 1))
    return {
        "post_id": post_id,
        "error": "unexpected",
        "identified_themes": [],
        "theme_token_probabilities": {},
        "sentiment": None,
    }


def append_jsonl(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


async def run_pipeline(args: argparse.Namespace) -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        logger.error("OPENAI_API_KEY is not set")
        sys.exit(1)

    base_url = os.environ.get("OPENAI_BASE_URL")
    client = AsyncOpenAI(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=base_url if base_url and base_url.strip() else None,
        timeout=120.0,
    )

    allowed_categories, topics_by_category = load_category_mapping(Path(args.mapping))
    all_posts = load_tiered_posts(Path(args.input))
    output_path = Path(args.output)

    processed = get_processed_ids(output_path)
    pending = [p for p in all_posts if p["post_id"] not in processed]
    if args.test_limit > 0:
        pending = pending[: args.test_limit]

    logger.info("Total posts in file: %s | Already done: %s | To process: %s", len(all_posts), len(processed), len(pending))
    if not pending:
        return

    model = args.model

    # --- Stage 1: category batches ---
    category_by_post: Dict[str, Dict[str, Any]] = {}
    batch_size = max(1, args.category_batch_size)
    for i in range(0, len(pending), batch_size):
        batch = pending[i : i + batch_size]
        logger.info("Category batch %s-%s (%s posts)", i + 1, i + len(batch), len(batch))
        results = await classify_category_batch(
            client=client,
            model=model,
            posts_slice=batch,
            allowed_categories=allowed_categories,
            max_text_chars=args.max_text_chars,
            max_retries=args.max_retries,
            retry_delay=args.retry_delay,
        )
        for r in results:
            category_by_post[r["post_id"]] = r

    # --- Stage 2: topics in parallel ---
    sem = asyncio.Semaphore(max(1, args.concurrent_topics))

    async def one_post(p: Dict[str, Any]) -> Dict[str, Any]:
        cid = p["post_id"]
        cat_info = category_by_post.get(cid, {})
        cat = cat_info.get("category")
        topics = topics_by_category.get(cat) if cat else []
        if not cat or not topics:
            topic_result: Dict[str, Any] = {
                "identified_themes": [],
                "theme_token_probabilities": {},
                "sentiment": None,
                "error": "no_valid_category" if not cat else "no_topics_for_category",
            }
        else:
            topic_result = await extract_topics_for_post(
                client=client,
                model=model,
                post_id=cid,
                text=p["text"],
                topic_universe=topics,
                semaphore=sem,
                max_retries=args.max_retries,
                retry_delay=args.retry_delay,
                max_text_chars=args.max_text_chars,
            )

        row: Dict[str, Any] = {
            "post_id": cid,
            "profile_id": p["profile_id"],
            "occupation": p.get("occupation"),
            "category": cat,
            "category_confidence": cat_info.get("category_confidence"),
            "category_rationale": cat_info.get("brief_rationale"),
            "identified_topics": topic_result.get("identified_themes", []),
            "topic_token_probabilities": topic_result.get("theme_token_probabilities", {}),
            "sentiment": topic_result.get("sentiment"),
            "topic_error": topic_result.get("error"),
        }
        if not args.omit_full_text:
            row["text"] = p["text"]
        else:
            row["text_preview"] = p["text"][:500]
        append_jsonl(output_path, row)
        return row

    chunk = args.concurrent_topics * 2
    for j in range(0, len(pending), chunk):
        part = pending[j : j + chunk]
        await asyncio.gather(*(one_post(p) for p in part))
        logger.info("Topic stage: wrote %s / %s", min(j + chunk, len(pending)), len(pending))

    logger.info("Done. Output: %s", output_path)


def main() -> None:
    root = Path(__file__).resolve().parents[3]
    default_input = root / "scripts/Linkedin_Posts_Extraction/tiered_posts_grouped/tier_1_authored.json"
    default_mapping = root / "scripts/LInkedin_Category_Topic_Extraction/tiered_posts_grouped/discovered_category_topic_mapping.json"
    default_output = root / "scripts/LInkedin_Category_Topic_Extraction/tiered_posts_grouped/post_topic_labels.jsonl"

    parser = argparse.ArgumentParser(description="LinkedIn post category + topic labeling (two-stage LLM).")
    parser.add_argument("--input", type=str, default=str(default_input), help="Tiered posts JSON path")
    parser.add_argument(
        "--mapping",
        type=str,
        default=str(default_mapping),
        help="discovered_category_topic_mapping.json (categories[])",
    )
    parser.add_argument("--output", type=str, default=str(default_output), help="Output JSONL path")
    parser.add_argument("--model", type=str, default=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"))
    parser.add_argument("--category-batch-size", type=int, default=12, help="Posts per category LLM call")
    parser.add_argument("--concurrent-topics", type=int, default=8, help="Max parallel topic extractions")
    parser.add_argument("--max-text-chars", type=int, default=6000, help="Truncate post text for prompts")
    parser.add_argument("--max-retries", type=int, default=5)
    parser.add_argument("--retry-delay", type=float, default=2.0)
    parser.add_argument("--test-limit", type=int, default=0, help="If >0, only process first N pending posts")
    parser.add_argument("--omit-full-text", action="store_true", help="Do not store full post body in JSONL")
    args = parser.parse_args()
    asyncio.run(run_pipeline(args))


if __name__ == "__main__":
    main()
