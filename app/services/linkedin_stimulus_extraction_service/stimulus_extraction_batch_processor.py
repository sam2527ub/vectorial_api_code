"""Async batch processing: tier-1 stimulus+category, tier-2 category-only.

Production notes:
- Supports bounded concurrency across batches.
- Supports a single repair attempt when model output misses ids.
- Does not fail the whole tier when a few ids are missing; caller can decide policy.
"""

from __future__ import annotations

import json
import logging
import asyncio
from pathlib import Path
from typing import Any, Dict, List, Tuple, TypeVar, Awaitable

from app.services.linkedin_theme_discovery_service.tier_production_config import llm_retry_for_tier
from .errors import StimulusExtractionError
from .stimulus_extraction_config import get_stimulus_extraction_config
from .stimulus_data import build_results_skeleton_posts, load_tier1_flat, load_tier2_flat
from .stimulus_extraction_llm import call_stimulus_json_via_gateway

log = logging.getLogger(__name__)

_PKG = Path(__file__).resolve().parent


def _read_prompt(name: str) -> str:
    p = _PKG / "prompts" / name
    return p.read_text(encoding="utf-8").strip()


def _compute_cost(in_tok: int, out_tok: int) -> float:
    c = get_stimulus_extraction_config()
    return (in_tok / 1_000_000) * c.price_input_per_1m + (out_tok / 1_000_000) * c.price_output_per_1m


# Gateway return does not always include token usage; counters stay 0 in v1.
async def _call_t1_batch(batch_idx: int, system: str, user: str, model: str) -> Dict[str, Any]:
    cfg = get_stimulus_extraction_config()
    return await call_stimulus_json_via_gateway(
        context_id=f"stim_t1_b{batch_idx}",
        system=system,
        user=user,
        max_tokens=cfg.tier1.completion_max_tokens,
        is_tier2=False,
        model=model,
        llm_retry=llm_retry_for_tier(False),
    )


async def _call_t2_batch(batch_idx: int, system: str, user: str, model: str) -> Dict[str, Any]:
    cfg = get_stimulus_extraction_config()
    return await call_stimulus_json_via_gateway(
        context_id=f"stim_t2_b{batch_idx}",
        system=system,
        user=user,
        max_tokens=cfg.tier2.completion_max_tokens,
        is_tier2=True,
        model=model,
        llm_retry=llm_retry_for_tier(True),
    )


def _parse_t1_batch(raw: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    ex = raw.get("extracted_data")
    if not isinstance(ex, list):
        raise StimulusExtractionError('Expected JSON with "extracted_data" array (tier 1)')
    by_id: Dict[str, Dict[str, str]] = {}
    for row in ex:
        if not isinstance(row, dict):
            continue
        pid = str(row.get("post_id", "")).strip()
        st = (row.get("stimulus") or "").strip() if row.get("stimulus") is not None else ""
        cat = (row.get("category") or "").strip() if row.get("category") is not None else ""
        if pid and st and cat:
            by_id[pid] = {"stimulus": st, "category": cat}
    if not by_id and ex:
        raise StimulusExtractionError("Tier-1 batch: no valid extracted_data rows with post_id, stimulus, category")
    return by_id


def _parse_t2_batch(raw: Dict[str, Any]) -> Dict[str, str]:
    ex = raw.get("extracted_data")
    if not isinstance(ex, list):
        raise StimulusExtractionError('Expected JSON with "extracted_data" array (tier 2)')
    by_id: Dict[str, str] = {}
    for row in ex:
        if not isinstance(row, dict):
            continue
        pid = str(row.get("post_id", "")).strip()
        cat = (row.get("category") or "").strip() if row.get("category") is not None else ""
        if pid and cat:
            by_id[pid] = cat
    if not by_id and ex:
        raise StimulusExtractionError("Tier-2 batch: no valid extracted_data rows with post_id, category")
    return by_id


T = TypeVar("T")


async def _run_bounded_concurrency(
    coros: List[Tuple[int, Awaitable[T]]],
    max_concurrency: int,
) -> Dict[int, T]:
    sem = asyncio.Semaphore(max(1, int(max_concurrency)))
    out: Dict[int, T] = {}

    async def _wrap(idx: int, c: Awaitable[T]) -> None:
        async with sem:
            out[idx] = await c

    await asyncio.gather(*[_wrap(i, c) for (i, c) in coros])
    return out


def _missing_ids(expected: List[str], got: Dict[str, Any]) -> List[str]:
    miss = []
    for pid in expected:
        if pid not in got:
            miss.append(pid)
    return miss


def _repair_suffix(missing: List[str]) -> str:
    sample = missing[:25]
    more = "" if len(missing) <= 25 else f" (+{len(missing) - 25} more)"
    return (
        "\n\nIMPORTANT:\n"
        "- Your previous response missed some post_id values.\n"
        "- You MUST output extracted_data rows for EVERY post_id in this batch.\n"
        f"- Missing post_id list: {json.dumps(sample)}{more}\n"
        "- Do not invent new post_id values.\n"
    )


async def run_tier1_stimulus_extraction(
    *,
    tier_data: Dict[str, Any],
    source_file: str,
    room_id: str,
    allowed_categories: List[str],
    model: str,
) -> Dict[str, Any]:
    cfg = get_stimulus_extraction_config()
    bs = cfg.batch_size
    flat = load_tier1_flat(tier_data)
    if not flat:
        raise StimulusExtractionError("No tier-1 posts with text in filtered input")
    system = _read_prompt("tier1_system.txt")
    user_tpl = _read_prompt("tier1_user.txt")
    results_by_user: Dict[str, Any] = build_results_skeleton_posts(tier_data)
    total_in = 0
    total_out = 0
    total_processed = 0
    num_batches = (len(flat) + bs - 1) // bs
    for i in range(0, len(flat), bs):
        chunk = flat[i : i + bs]
        bnum = i // bs + 1
        api_batch = [{"post_id": pid, "body_text": body} for (_uid, pid, body) in chunk]
        user = user_tpl.format(
            allowed_categories_json=json.dumps(allowed_categories, indent=2),
            batch_json=json.dumps(api_batch, ensure_ascii=False, indent=2),
        )
        log.info(
            "Stimulus tier-1: batch %s/%s (posts %s–%s)",
            bnum,
            num_batches,
            i + 1,
            i + len(chunk),
        )
        raw = await _call_t1_batch(bnum, system, user, model)
        by_post = _parse_t1_batch(raw)
        for user_id, post_id, body_text in chunk:
            row = by_post.get(post_id)
            if not row:
                raise StimulusExtractionError(f"Missing model output for post_id={post_id!r} (tier-1 batch {bnum})")
            results_by_user.setdefault(user_id, {"profile_info": {}, "posts": []})
            if "posts" not in results_by_user[user_id]:
                results_by_user[user_id]["posts"] = []
            results_by_user[user_id]["posts"].append(
                {
                    "post_id": post_id,
                    "body_text": body_text,
                    "stimulus": row["stimulus"],
                    "category": row["category"],
                }
            )
            total_processed += 1
    cost = _compute_cost(total_in, total_out)
    return {
        "room_id": room_id,
        "source_file": source_file,
        "results_by_user": results_by_user,
        "total_posts_processed": total_processed,
        "total_prompt_tokens": total_in,
        "total_completion_tokens": total_out,
        "cost_usd": round(cost, 6),
    }


async def run_tier1_stimulus_extraction_chunk(
    *,
    flat: List[Tuple[str, str, str]],
    allowed_categories: List[str],
    model: str,
    start_batch_index: int,
    num_batches: int,
    max_concurrency: int,
    batch_size: int,
) -> Tuple[Dict[str, Dict[str, str]], Dict[str, str]]:
    """
    Process a range of tier-1 batches from a pre-flattened list.
    Returns (by_post_id -> {stimulus,category}, failures_by_post_id -> error).
    """
    if not flat:
        return {}, {}
    system = _read_prompt("tier1_system.txt")
    user_tpl = _read_prompt("tier1_user.txt")
    failures: Dict[str, str] = {}

    bs = max(1, int(batch_size))
    total_batches = (len(flat) + bs - 1) // bs
    start = max(0, start_batch_index)
    end = min(total_batches, start + max(1, num_batches))

    coros: List[Tuple[int, asyncio.Future[Dict[str, Any]]]] = []
    batch_expected: Dict[int, List[str]] = {}
    batch_payload: Dict[int, str] = {}
    for b in range(start, end):
        i = b * bs
        chunk = flat[i : i + bs]
        api_batch = [{"post_id": pid, "body_text": body} for (_uid, pid, body) in chunk]
        user = user_tpl.format(
            allowed_categories_json=json.dumps(allowed_categories, indent=2),
            batch_json=json.dumps(api_batch, ensure_ascii=False, indent=2),
        )
        batch_expected[b] = [pid for (_uid, pid, _body) in chunk]
        batch_payload[b] = user
        coros.append((b, _call_t1_batch(b + 1, system, user, model)))  # 1-index for logs

    raw_by_batch = await _run_bounded_concurrency(coros, max_concurrency=max_concurrency)

    out_by_id: Dict[str, Dict[str, str]] = {}
    for b, raw in raw_by_batch.items():
        try:
            by_post = _parse_t1_batch(raw)
        except Exception as e:
            for pid in batch_expected.get(b, []):
                failures[pid] = f"tier1_parse_error: {e}"
            continue
        miss = _missing_ids(batch_expected.get(b, []), by_post)
        if miss:
            # Repair attempt once
            try:
                repaired_raw = await _call_t1_batch(
                    b + 1,
                    system,
                    batch_payload[b] + _repair_suffix(miss),
                    model,
                )
                repaired = _parse_t1_batch(repaired_raw)
                by_post.update(repaired)
            except Exception as e:
                # keep going; mark missing as failed below
                for pid in miss:
                    failures[pid] = f"tier1_repair_failed: {e}"
        # Merge successes; mark remaining missing as failures
        for pid in batch_expected.get(b, []):
            row = by_post.get(pid)
            if row:
                out_by_id[pid] = row
            else:
                failures.setdefault(pid, "tier1_missing_output")
    return out_by_id, failures


async def run_tier2_category_extraction_chunk(
    *,
    flat: List[Tuple[str, str, str, str]],
    allowed_categories: List[str],
    model: str,
    start_batch_index: int,
    num_batches: int,
    max_concurrency: int,
    batch_size: int,
) -> Tuple[Dict[str, str], Dict[str, str]]:
    """
    Process a range of tier-2 batches from a pre-flattened list.
    Returns (by_post_id -> category, failures_by_post_id -> error).
    """
    if not flat:
        return {}, {}
    system = _read_prompt("tier2_system.txt")
    user_tpl = _read_prompt("tier2_user.txt")
    failures: Dict[str, str] = {}

    bs = max(1, int(batch_size))
    total_batches = (len(flat) + bs - 1) // bs
    start = max(0, start_batch_index)
    end = min(total_batches, start + max(1, num_batches))

    coros: List[Tuple[int, asyncio.Future[Dict[str, Any]]]] = []
    batch_expected: Dict[int, List[str]] = {}
    batch_payload: Dict[int, str] = {}
    for b in range(start, end):
        i = b * bs
        chunk = flat[i : i + bs]
        api_batch = [
            {"post_id": pid, "body_text": response}
            for _uid, pid, response, _stimulus in chunk
        ]
        user = user_tpl.format(
            allowed_categories_json=json.dumps(allowed_categories, indent=2),
            batch_json=json.dumps(api_batch, ensure_ascii=False, indent=2),
        )
        batch_expected[b] = [pid for (_uid, pid, _resp, _stim) in chunk]
        batch_payload[b] = user
        coros.append((b, _call_t2_batch(b + 1, system, user, model)))

    raw_by_batch = await _run_bounded_concurrency(coros, max_concurrency=max_concurrency)

    out_by_id: Dict[str, str] = {}
    for b, raw in raw_by_batch.items():
        try:
            by_post = _parse_t2_batch(raw)
        except Exception as e:
            for pid in batch_expected.get(b, []):
                failures[pid] = f"tier2_parse_error: {e}"
            continue
        miss = _missing_ids(batch_expected.get(b, []), by_post)
        if miss:
            try:
                repaired_raw = await _call_t2_batch(
                    b + 1,
                    system,
                    batch_payload[b] + _repair_suffix(miss),
                    model,
                )
                repaired = _parse_t2_batch(repaired_raw)
                by_post.update(repaired)
            except Exception as e:
                for pid in miss:
                    failures[pid] = f"tier2_repair_failed: {e}"
        for pid in batch_expected.get(b, []):
            cat = by_post.get(pid)
            if cat:
                out_by_id[pid] = cat
            else:
                failures.setdefault(pid, "tier2_missing_output")
    return out_by_id, failures


async def run_tier2_category_extraction(
    *,
    tier_data: Dict[str, Any],
    source_file: str,
    room_id: str,
    allowed_categories: List[str],
    model: str,
) -> Dict[str, Any]:
    cfg = get_stimulus_extraction_config()
    bs = cfg.batch_size
    flat = load_tier2_flat(tier_data)
    if not flat:
        raise StimulusExtractionError("No tier-2 interactions with non-empty response in filtered input")
    system = _read_prompt("tier2_system.txt")
    user_tpl = _read_prompt("tier2_user.txt")
    results_by_user: Dict[str, Any] = build_results_skeleton_posts(tier_data)
    total_in = 0
    total_out = 0
    total_processed = 0
    num_batches = (len(flat) + bs - 1) // bs
    for i in range(0, len(flat), bs):
        chunk = flat[i : i + bs]
        bnum = i // bs + 1
        api_batch = [
            {"post_id": pid, "body_text": response}
            for _uid, pid, response, _stimulus in chunk
        ]
        user = user_tpl.format(
            allowed_categories_json=json.dumps(allowed_categories, indent=2),
            batch_json=json.dumps(api_batch, ensure_ascii=False, indent=2),
        )
        log.info(
            "Stimulus tier-2: batch %s/%s (rows %s–%s)",
            bnum,
            num_batches,
            i + 1,
            i + len(chunk),
        )
        raw = await _call_t2_batch(bnum, system, user, model)
        by_post = _parse_t2_batch(raw)
        for user_id, post_id, response, stimulus in chunk:
            cat = by_post.get(post_id)
            if not cat:
                raise StimulusExtractionError(
                    f"Missing model output for post_id={post_id!r} (tier-2 batch {bnum})"
                )
            results_by_user.setdefault(user_id, {"profile_info": {}, "posts": []})
            if "posts" not in results_by_user[user_id]:
                results_by_user[user_id]["posts"] = []
            results_by_user[user_id]["posts"].append(
                {
                    "post_id": post_id,
                    "body_text": response,
                    "stimulus": stimulus,
                    "category": cat,
                }
            )
            total_processed += 1
    cost = _compute_cost(total_in, total_out)
    return {
        "room_id": room_id,
        "source_file": source_file,
        "results_by_user": results_by_user,
        "total_posts_processed": total_processed,
        "total_prompt_tokens": total_in,
        "total_completion_tokens": total_out,
        "cost_usd": round(cost, 6),
    }
