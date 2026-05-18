#!/usr/bin/env python3
"""
Local LinkedIn post → per-topic labels (LLM-as-annotator “ground truth” extraction).

**All data I/O is local** (no W&B). Only the OpenAI-compatible chat API is remote.

Pipeline
--------
1. Read a contextual JSON → ``results_by_user`` → each row under ``posts[]`` with
   ``body_text``, ``category``, ``post_id``, ``stimulus`` (same layout for tier-1 authored
   posts or tier-2 interactions). Use ``--preset tier1`` (default) or ``--preset tier2``;
   tier-2 defaults: ``contextual_stimulus_extraction_tier2.json`` from
   ``extract_stimulus_category.py --tier 2`` or ``extract_tier2_interaction_category.py``
   (both read ``filtered_kept_tier2.json``; do not use unfiltered tier_2_reposts_and_comments).
2. Read a category→topics mapping JSON (``categories`` array). **Tier1 default:**
   ``discovered_category_topic_mapping.json``. **Tier2 default:**
   ``discovered_category_topic_mapping_tier2.json`` if present, else tier1 discovered (same path).
   Discovered files may include ``metadata`` / theme lists; only ``categories[]`` is used. Override
   with ``--mapping`` for a custom path.
3. For **each** topic, one short LLM call: does this post discuss this topic? Answer Yes/No.
   (``include_post_body=False`` omits ``body_text``; the model only sees category in that case.)
4. Logprobs: ``logprobs=True``, ``top_logprobs=5``; take the **best** (max) logprob for
   yes/y and for no/n, then \(\exp\) and renormalize per topic so ``P_yes + P_no = 1``.
5. Per post: ``topic_probabilities_before_normalisation`` = per-topic ``P_yes`` after
   \(\exp(\text{logprob})\) and **per-call** normalize (\(P_\text{yes}+P_\text{no}=1\)).
   ``topic_probabilities`` = **softmax** over stored ``logprob_yes`` across category topics
   (stable max trick; sums to 1). ``topic_logprobs`` = raw best ``logprob_yes`` /
   ``logprob_no`` from the API (with ``top_logprobs``), or ``-10`` fallback when absent.

Writes **one JSON file** with the **same shape** as the input: ``room_id``, ``source_file``,
``results_by_user`` → ``profile_id`` → ``posts[]`` where each post keeps ``post_id``,
``body_text``, ``stimulus``, ``category`` and gains ``topic_probabilities``,
``topic_probabilities_before_normalisation``, ``topic_logprobs``, ``predicted_themes``,
``topics_ordered``, etc. Adds ``topic_classification_meta`` at the top level. Resume: if ``--output``
already exists, it is loaded and only posts missing topic fields are filled in. Saves every
``--save-every`` posts (atomic write). Default paths under ``tiered_posts_grouped/``.

Usage (from repo root):
  python scripts/LInkedin_Category_Topic_Extraction/post_topic_classification/contextual_post_topic_presence.py
  python .../contextual_post_topic_presence.py --preset tier2

Requires ``OPENAI_API_KEY``; optional ``OPENAI_BASE_URL`` (e.g. gateway).
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import logging
import math
import os
import sys
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

# Fallback logprob when yes/no never appear in the completion stream (finite for exp)
_LP_FALLBACK = -10.0


def model_supports_logprobs(model_name: str) -> bool:
    m = model_name.lower()
    return any(x in m for x in ("gpt-4", "gpt-3.5", "gpt-4o", "gpt-4-turbo", "gpt-oss", "oss"))


def load_category_mapping(path: Path) -> Dict[str, List[str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    topics_by_category: Dict[str, List[str]] = {}
    for row in data.get("categories", []):
        cat = row.get("category")
        topics = row.get("topics") or []
        if not cat:
            continue
        topics_by_category[cat] = list(topics)
    if not topics_by_category:
        raise ValueError(f"No categories in {path}")
    return topics_by_category


def resolve_default_mapping_path(tiered: Path, preset: str, mapping_override: Optional[str]) -> Path:
    """Tier1 → ``discovered_category_topic_mapping.json``. Tier2 → tier2 discovered if present, else tier1."""
    if mapping_override and str(mapping_override).strip():
        return Path(mapping_override).expanduser().resolve()
    tier1_disc = tiered / "discovered_category_topic_mapping.json"
    tier2_disc = tiered / "discovered_category_topic_mapping_tier2.json"
    if preset == "tier2":
        if tier2_disc.is_file():
            return tier2_disc
        if tier1_disc.is_file():
            logger.warning(
                "Tier2 mapping %s not found; using %s",
                tier2_disc.name,
                tier1_disc.name,
            )
            return tier1_disc
        raise FileNotFoundError(
            f"No discovered mapping in {tiered}: need {tier2_disc.name} or {tier1_disc.name}"
        )
    if tier1_disc.is_file():
        return tier1_disc
    raise FileNotFoundError(f"Tier1 requires {tier1_disc}")


def softmax_over_logprob_yes(
    topics: List[str],
    topic_logprobs: Dict[str, Dict[str, float]],
) -> Dict[str, float]:
    """Numerically stable softmax on ``logprob_yes``; values sum to 1 over ``topics`` order."""
    if not topics:
        return {}
    lps: List[float] = []
    for t in topics:
        row = topic_logprobs.get(t) or {}
        v = float(row.get("logprob_yes", _LP_FALLBACK))
        lps.append(v if math.isfinite(v) else _LP_FALLBACK)
    m = max(lps)
    exps = [math.exp(lp - m) for lp in lps]
    s = sum(exps)
    if s <= 0 or not math.isfinite(s):
        u = 1.0 / len(topics)
        return {t: u for t in topics}
    return {t: e / s for t, e in zip(topics, exps)}


def load_contextual_payload(path: Path) -> Dict[str, Any]:
    """Full document: room_id, source_file, results_by_user, ...

    Accepts ``users`` as an alias for ``results_by_user`` (some filtered bundles use that key).
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    rb = data.get("results_by_user")
    if isinstance(rb, dict):
        return data
    users = data.get("users")
    if isinstance(users, dict):
        out = dict(data)
        out["results_by_user"] = users
        return out
    raise ValueError("Expected top-level key 'results_by_user' or 'users' (object)")


def iter_mutable_posts(payload: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    """Stable order: (profile_id, post_dict) references for in-place updates."""
    out: List[Tuple[str, Dict[str, Any]]] = []
    by_user = payload.get("results_by_user") or {}
    for profile_id, bundle in by_user.items():
        if not isinstance(bundle, dict):
            continue
        for post in bundle.get("posts") or []:
            if isinstance(post, dict) and post.get("post_id"):
                out.append((str(profile_id), post))
    return out


def classification_done(post: Dict[str, Any]) -> bool:
    if post.get("topic_classification_error"):
        return True
    tp = post.get("topic_probabilities")
    if isinstance(tp, dict) and len(tp) > 0:
        return True
    legacy = post.get("topic_yes_probability")
    return isinstance(legacy, dict) and len(legacy) > 0


def has_successful_topic_labels(post: Dict[str, Any]) -> bool:
    """True only for completed runs with real topic probs (not terminal error-only rows)."""
    if post.get("topic_classification_error"):
        return False
    tp = post.get("topic_probabilities")
    if isinstance(tp, dict) and len(tp) > 0:
        return True
    legacy = post.get("topic_yes_probability")
    return isinstance(legacy, dict) and len(legacy) > 0


def mark_unknown_category_on_post(post: Dict[str, Any]) -> None:
    post.pop("cluster", None)
    post["topics_ordered"] = []
    post["topic_probabilities"] = {}
    post["topic_probabilities_before_normalisation"] = {}
    post["topic_logprobs"] = {}
    post["predicted_themes"] = []
    post["num_topics_classified"] = 0
    post["classification_method"] = "per_topic_yes_no_logprobs"
    post["topic_classification_error"] = "unknown_category_or_no_topics_in_mapping"
    post.pop("topic_yes_probability", None)
    post.pop("topic_yes_probability_softmax", None)


def mark_missing_body_on_post(post: Dict[str, Any]) -> None:
    post.pop("cluster", None)
    post["topics_ordered"] = []
    post["topic_probabilities"] = {}
    post["topic_probabilities_before_normalisation"] = {}
    post["topic_logprobs"] = {}
    post["predicted_themes"] = []
    post["num_topics_classified"] = 0
    post["classification_method"] = "per_topic_yes_no_logprobs"
    post["topic_classification_error"] = "missing_body_text"
    post.pop("topic_yes_probability", None)
    post.pop("topic_yes_probability_softmax", None)


def merge_classification_into_post(post: Dict[str, Any], out: Dict[str, Any]) -> None:
    """Attach classifier outputs (review-style keys) to the post object."""
    post.pop("cluster", None)
    for k in (
        "topics_ordered",
        "topic_probabilities",
        "topic_probabilities_before_normalisation",
        "topic_logprobs",
        "predicted_themes",
        "num_topics_classified",
        "classification_method",
        "prediction_uses_post_body",
    ):
        if k in out and out[k] is not None:
            post[k] = out[k]
    post.pop("topic_yes_probability", None)
    post.pop("topic_yes_probability_softmax", None)
    pe = out.get("per_topic_errors") or {}
    if pe:
        post["per_topic_errors"] = pe
        post["topic_classification_error"] = "partial_topic_errors"
    else:
        post.pop("per_topic_errors", None)
        if post.get("topic_classification_error") == "partial_topic_errors":
            post.pop("topic_classification_error", None)


def save_contextual_json(payload: Dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp.replace(path)


def _yes_no_logprobs_from_completion(logprobs_data: Any) -> Tuple[float, float, Optional[str], Optional[str]]:
    """
    Best (max) logprob for yes/y and for no/n over the full completion stream, including
    ``top_logprobs`` alternates at each step. Missing side → ``_LP_FALLBACK`` so downstream
    exp/normalize stays finite.
    """
    yes_lp = float("-inf")
    no_lp = float("-inf")
    yes_token: Optional[str] = None
    no_token: Optional[str] = None

    if not logprobs_data or not hasattr(logprobs_data, "content"):
        return _LP_FALLBACK, _LP_FALLBACK, None, None

    def _lp(val: Any) -> float:
        if val is None:
            return float("-inf")
        try:
            x = float(val)
            return x if math.isfinite(x) else float("-inf")
        except (TypeError, ValueError):
            return float("-inf")

    try:
        for token_info in logprobs_data.content:
            lp = _lp(getattr(token_info, "logprob", None))
            tok = getattr(token_info, "token", "") or ""
            clean = tok.lower().strip().strip('"').strip("'")
            if clean in ("yes", "y"):
                if lp > yes_lp:
                    yes_lp = lp
                    yes_token = tok
            elif clean in ("no", "n"):
                if lp > no_lp:
                    no_lp = lp
                    no_token = tok

            alts = getattr(token_info, "top_logprobs", None) or []
            for alt in alts:
                alt_tok = getattr(alt, "token", "") or ""
                ac = alt_tok.lower().strip().strip('"').strip("'")
                alt_lp = _lp(getattr(alt, "logprob", None))
                if ac in ("yes", "y"):
                    if alt_lp > yes_lp:
                        yes_lp = alt_lp
                        yes_token = alt_tok
                elif ac in ("no", "n"):
                    if alt_lp > no_lp:
                        no_lp = alt_lp
                        no_token = alt_tok
    except Exception as e:
        logger.error("logprob parse error: %s", e)
        return _LP_FALLBACK, _LP_FALLBACK, None, None

    if not math.isfinite(yes_lp):
        yes_lp = _LP_FALLBACK
    if not math.isfinite(no_lp):
        no_lp = _LP_FALLBACK

    return yes_lp, no_lp, yes_token, no_token


def _normalize_yes_no_probs(logprob_yes: float, logprob_no: float) -> Tuple[float, float]:
    """P_yes = exp(ly)/(exp(ly)+exp(ln)); uses finite fallbacks so exp never gets NaN."""
    ly = float(logprob_yes) if math.isfinite(logprob_yes) else _LP_FALLBACK
    ln = float(logprob_no) if math.isfinite(logprob_no) else _LP_FALLBACK
    py = math.exp(ly)
    pn = math.exp(ln)
    total = py + pn
    if total > 0 and math.isfinite(total):
        return py / total, pn / total
    return 0.5, 0.5


async def get_topic_logprobs(
    client: AsyncOpenAI,
    model: str,
    post_text: str,
    topic: str,
    category: str,
    semaphore: asyncio.Semaphore,
    max_retries: int,
    retry_delay: float,
    max_text_chars: int,
    *,
    include_post_body: bool = True,
) -> Dict[str, Any]:
    """
    One API call: Yes/No for this topic; probs from best yes/no logprobs + normalize.

    When ``include_post_body`` is False, the model does not see ``body_text``; only category
    and a generic per-topic question (e.g. SGO no-body scoring).
    """
    if include_post_body:
        system_msg = "You are a precise topic classifier for professional social posts. Answer only Yes or No."
        body = (post_text or "")[:max_text_chars]
        user_msg = (
            f'Category context: "{category}"\n\n'
            f'Analyze this LinkedIn-style post and determine if it discusses the topic "{topic}".\n\n'
            f'Post:\n"""{body}"""\n\n'
            f'Does this post discuss the topic "{topic}"?\n'
            f'Answer with ONLY "Yes" or "No".'
        )
    else:
        system_msg = (
            "You estimate whether a LinkedIn post (which you do NOT see) would likely discuss a topic, "
            "given only its category. Answer only Yes or No."
        )
        user_msg = (
            f'Category: "{category}"\n\n'
            f'Question: Would a typical post in this category likely discuss the topic "{topic}"?\n'
            f'You do not have the post body. Answer with ONLY "Yes" or "No".'
        )

    last_err: Optional[Exception] = None
    for attempt in range(max_retries):
        async with semaphore:
            try:
                if model_supports_logprobs(model):
                    try:
                        response = await client.chat.completions.create(
                            model=model,
                            messages=[
                                {"role": "system", "content": system_msg},
                                {"role": "user", "content": user_msg},
                            ],
                            max_tokens=5,
                            temperature=0,
                            logprobs=True,
                            top_logprobs=5,
                            timeout=90.0,
                        )
                        ch = response.choices[0]
                        logprobs_data = getattr(ch, "logprobs", None)
                        yes_lp, no_lp, yes_tok, no_tok = _yes_no_logprobs_from_completion(logprobs_data)
                        prob_yes, prob_no = _normalize_yes_no_probs(yes_lp, no_lp)

                        return {
                            "yes": prob_yes,
                            "no": prob_no,
                            "logprob_yes": yes_lp,
                            "logprob_no": no_lp,
                            "token_yes": yes_tok,
                            "token_no": no_tok,
                            "error": None,
                        }
                    except Exception as logprob_error:
                        err_s = str(logprob_error).lower()
                        if "logprob" in err_s or "403" in err_s:
                            response = await client.chat.completions.create(
                                model=model,
                                messages=[
                                    {"role": "system", "content": system_msg},
                                    {"role": "user", "content": user_msg},
                                ],
                                max_tokens=5,
                                temperature=0,
                                timeout=90.0,
                            )
                            answer_text = (response.choices[0].message.content or "").strip().lower()
                            is_yes = "yes" in answer_text or answer_text.startswith("y")
                            prob_yes = 0.9 if is_yes else 0.1
                            prob_no = 0.1 if is_yes else 0.9
                            return {
                                "yes": prob_yes,
                                "no": prob_no,
                                "logprob_yes": math.log(prob_yes) if prob_yes > 0 else _LP_FALLBACK,
                                "logprob_no": math.log(prob_no) if prob_no > 0 else _LP_FALLBACK,
                                "token_yes": "Yes" if is_yes else None,
                                "token_no": "No" if not is_yes else None,
                                "error": "logprobs_unavailable_fallback",
                            }
                        raise
                else:
                    response = await client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": system_msg},
                            {"role": "user", "content": user_msg},
                        ],
                        max_tokens=5,
                        temperature=0,
                        timeout=90.0,
                    )
                    answer_text = (response.choices[0].message.content or "").strip().lower()
                    is_yes = "yes" in answer_text or answer_text.startswith("y")
                    prob_yes = 0.9 if is_yes else 0.1
                    prob_no = 0.1 if is_yes else 0.9
                    return {
                        "yes": prob_yes,
                        "no": prob_no,
                        "logprob_yes": math.log(prob_yes) if prob_yes > 0 else _LP_FALLBACK,
                        "logprob_no": math.log(prob_no) if prob_no > 0 else _LP_FALLBACK,
                        "token_yes": "Yes" if is_yes else None,
                        "token_no": "No" if not is_yes else None,
                        "error": "model_without_logprobs_fallback",
                    }
            except Exception as e:
                last_err = e
                logger.warning("get_topic_logprobs attempt %s topic=%r: %s", attempt + 1, topic[:40], e)
                if attempt == max_retries - 1:
                    return {
                        "yes": 0.0,
                        "no": 0.0,
                        "logprob_yes": _LP_FALLBACK,
                        "logprob_no": _LP_FALLBACK,
                        "token_yes": None,
                        "token_no": None,
                        "error": str(e),
                    }
                await asyncio.sleep(retry_delay * (attempt + 1))

    return {
        "yes": 0.0,
        "no": 0.0,
        "logprob_yes": _LP_FALLBACK,
        "logprob_no": _LP_FALLBACK,
        "token_yes": None,
        "token_no": None,
        "error": str(last_err) if last_err else "unexpected",
    }


async def process_single_post(
    client: AsyncOpenAI,
    model: str,
    post: Dict[str, Any],
    topics: List[str],
    semaphore: asyncio.Semaphore,
    prob_threshold: float,
    max_retries: int,
    retry_delay: float,
    max_text_chars: int,
    *,
    include_post_body: bool = True,
) -> Dict[str, Any]:
    """
    Parallel Yes/No+logprob calls; then before-norm P(yes), softmax topic_probabilities on logprob_yes.

    Set ``include_post_body=False`` for SGO-style evaluation: same API shape, but prompts omit
    ``body_text`` so the model cannot read the post it is scored against.
    """
    text = (post.get("body_text") or "") if include_post_body else ""
    cat = post["category"]
    tasks = [
        get_topic_logprobs(
            client,
            model,
            text,
            topic,
            cat,
            semaphore,
            max_retries=max_retries,
            retry_delay=retry_delay,
            max_text_chars=max_text_chars,
            include_post_body=include_post_body,
        )
        for topic in topics
    ]
    topic_results = await asyncio.gather(*tasks, return_exceptions=True)

    before_norm: Dict[str, float] = {}
    topic_logprobs: Dict[str, Dict[str, float]] = {}
    predicted_themes: List[str] = []
    per_topic_errors: Dict[str, str] = {}

    for topic, result in zip(topics, topic_results):
        if isinstance(result, Exception):
            logger.warning("topic %r: %s", topic[:50], result)
            before_norm[topic] = 0.0
            topic_logprobs[topic] = {"logprob_yes": _LP_FALLBACK, "logprob_no": _LP_FALLBACK}
            per_topic_errors[topic] = str(result)
            continue

        if result.get("error") and result["error"] not in (
            None,
            "logprobs_unavailable_fallback",
            "model_without_logprobs_fallback",
        ):
            per_topic_errors[topic] = str(result["error"])

        py = float(result.get("yes", 0.0))
        before_norm[topic] = py
        topic_logprobs[topic] = {
            "logprob_yes": float(result.get("logprob_yes", _LP_FALLBACK)),
            "logprob_no": float(result.get("logprob_no", _LP_FALLBACK)),
        }
        if py > prob_threshold:
            predicted_themes.append(topic)

    topic_probabilities = softmax_over_logprob_yes(topics, topic_logprobs)

    return {
        "topics_ordered": topics,
        "topic_probabilities": topic_probabilities,
        "topic_probabilities_before_normalisation": before_norm,
        "topic_logprobs": topic_logprobs,
        "predicted_themes": predicted_themes,
        "num_topics_classified": len(topics),
        "per_topic_errors": per_topic_errors,
        "classification_method": "per_topic_yes_no_logprobs",
        "prediction_uses_post_body": include_post_body,
    }


async def run_pipeline(args: argparse.Namespace) -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        logger.error("OPENAI_API_KEY is not set")
        sys.exit(1)

    input_path = Path(args.input).expanduser().resolve()
    mapping_path = Path(args.mapping).expanduser().resolve()
    if not input_path.is_file():
        logger.error("Input JSON not found: %s", input_path)
        sys.exit(1)
    if not mapping_path.is_file():
        logger.error("Category mapping JSON not found: %s", mapping_path)
        sys.exit(1)

    base_url = os.environ.get("OPENAI_BASE_URL")
    client = AsyncOpenAI(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=base_url if base_url and base_url.strip() else None,
        timeout=120.0,
    )

    topics_by_category = load_category_mapping(mapping_path)
    output_path = Path(args.output).expanduser().resolve()

    if output_path.is_file():
        try:
            working = load_contextual_payload(output_path)
            logger.info("Resuming from existing output: %s", output_path)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("Could not load output (%s); copying from input.", e)
            working = copy.deepcopy(load_contextual_payload(input_path))
    else:
        working = copy.deepcopy(load_contextual_payload(input_path))

    all_refs = iter_mutable_posts(working)
    total_posts = len(all_refs)
    had_labels_before_scan = sum(1 for _, p in all_refs if has_successful_topic_labels(p))

    pending_llm: List[Dict[str, Any]] = []
    skipped_no_body = 0
    marked_unknown = 0

    for _uid, post in all_refs:
        if classification_done(post):
            continue
        body = (post.get("body_text") or "").strip()
        if not body:
            skipped_no_body += 1
            mark_missing_body_on_post(post)
            continue
        cat = (post.get("category") or "").strip()
        topics = topics_by_category.get(cat) or []
        if not topics:
            mark_unknown_category_on_post(post)
            marked_unknown += 1
            continue
        pending_llm.append(post)

    if args.test_limit > 0:
        pending_llm = pending_llm[: args.test_limit]

    pending_count = len(pending_llm)
    init_mutations = marked_unknown + skipped_no_body
    terminal_after_scan = sum(1 for _, p in all_refs if classification_done(p))

    logger.info(
        "Local I/O — input=%s mapping=%s output=%s",
        input_path,
        mapping_path,
        output_path,
    )
    logger.info(
        "Posts in tree: %s | Already had topic labels (before scan): %s | To classify (LLM): %s | "
        "skipped empty body: %s | unknown category (no topics list for category): %s | "
        "terminal/done after scan: %s | concurrent API cap: %s",
        total_posts,
        had_labels_before_scan,
        pending_count,
        skipped_no_body,
        marked_unknown,
        terminal_after_scan,
        args.concurrent,
    )
    if marked_unknown > 0:
        missing_cats: Dict[str, int] = {}
        for _uid, post in all_refs:
            if post.get("topic_classification_error") != "unknown_category_or_no_topics_in_mapping":
                continue
            c = (post.get("category") or "").strip() or "(empty category)"
            missing_cats[c] = missing_cats.get(c, 0) + 1
        map_cats = list(topics_by_category.keys())
        logger.warning(
            "%s posts: post ``category`` not found in mapping (exact string match). "
            "Unique post categories (count): %s. "
            "Mapping defines %s categories (e.g. %s). "
            "Align ``categories[].category`` in %s with the ``category`` field on posts, or pass "
            "``--mapping`` to a JSON that matches.",
            marked_unknown,
            dict(sorted(missing_cats.items(), key=lambda x: -x[1])[:12]),
            len(map_cats),
            map_cats[:8],
            mapping_path.name,
        )

    meta = {
        "model": args.model,
        "prob_threshold": args.prob_threshold,
        "classification_method": "per_topic_yes_no_logprobs",
        "input_file": str(input_path),
        "mapping_file": str(mapping_path),
        "schema_note": (
            "topic_probabilities = softmax(logprob_yes) over category topics; "
            "topic_probabilities_before_normalisation = P_yes after each call's yes/no normalize"
        ),
    }
    working["topic_classification_meta"] = meta

    if init_mutations > 0:
        save_contextual_json(working, output_path)
        logger.info("Wrote initial pass (%s posts marked skip/error) → %s", init_mutations, output_path)

    if pending_count == 0:
        save_contextual_json(working, output_path)
        logger.info("Nothing left to classify. Wrote: %s", output_path)
        return

    sem = asyncio.Semaphore(max(1, args.concurrent))
    model = args.model
    save_every = max(1, args.save_every)

    for idx, post in enumerate(pending_llm, 1):
        cat = (post.get("category") or "").strip()
        topics = topics_by_category[cat]
        out = await process_single_post(
            client=client,
            model=model,
            post=post,
            topics=topics,
            semaphore=sem,
            prob_threshold=args.prob_threshold,
            max_retries=args.max_retries,
            retry_delay=args.retry_delay,
            max_text_chars=args.max_text_chars,
        )
        merge_classification_into_post(post, out)

        progress_every = max(1, args.progress_every)
        if idx % progress_every == 0 or idx == pending_count:
            pid = post.get("post_id", "?")
            pred = out.get("predicted_themes") or []
            logger.info(
                "Progress %s / %s | post_id=%s | predicted_themes=%s",
                idx,
                pending_count,
                pid,
                len(pred),
            )

        if idx % save_every == 0 or idx == pending_count:
            working["topic_classification_meta"] = {**meta, "last_saved_post_index": idx, "pending_total": pending_count}
            save_contextual_json(working, output_path)
            logger.info("Saved JSON: %s / %s → %s", idx, pending_count, output_path)

    working["topic_classification_meta"] = {
        **meta,
        "last_saved_post_index": pending_count,
        "pending_total": pending_count,
        "status": "complete",
    }
    save_contextual_json(working, output_path)
    logger.info("Done. Output: %s", output_path)


def main() -> None:
    root = Path(__file__).resolve().parents[3]
    tiered = root / "scripts/LInkedin_Category_Topic_Extraction/tiered_posts_grouped"
    tier1_input = tiered / "contextual_stimulus_extraction.json"
    tier1_output = tiered / "contextual_stimulus_extraction_with_topics.json"
    # Tier-2 filtered_kept pipeline: extract_stimulus_category.py --tier 2
    tier2_input = tiered / "contextual_stimulus_extraction_tier2.json"
    tier2_output = tiered / "contextual_stimulus_extraction_tier2_with_topics.json"
    # Tier 2: default input is contextual_stimulus_extraction_tier2.json (269 filtered rows).
    # Legacy unfiltered file tier2_interaction_category.json (374 rows) — avoid unless intentional.

    parser = argparse.ArgumentParser(
        description="Local: topic_probabilities + logprobs merged into contextual JSON (nested shape)."
    )
    parser.add_argument(
        "--preset",
        choices=("tier1", "tier2"),
        default="tier1",
        help=(
            "tier1: contextual_stimulus_extraction.json → _with_topics.json. "
            "tier2: contextual_stimulus_extraction_tier2.json → _with_topics.json "
            "(from filtered_kept_tier2.json via category extraction; not raw tier_2_reposts)."
        ),
    )
    parser.add_argument(
        "--input",
        type=str,
        default=None,
        help="Override input path (default depends on --preset)",
    )
    parser.add_argument(
        "--mapping",
        type=str,
        default=None,
        help=(
            "Category→topics JSON (``categories`` array). Default: "
            "discovered_category_topic_mapping.json (tier1) or "
            "discovered_category_topic_mapping_tier2.json (tier2), else tier1 file."
        ),
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Override output path (default depends on --preset)",
    )
    parser.add_argument(
        "--save-every",
        type=int,
        default=5,
        help="Write full JSON to --output every N classified posts (atomic replace); also on last post",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=5,
        help="Log progress line to the console every N classified posts; also on last post",
    )
    parser.add_argument("--model", type=str, default=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"))
    parser.add_argument(
        "--concurrent",
        type=int,
        default=10,
        help="Max parallel API calls globally (topics across posts share this cap)",
    )
    parser.add_argument(
        "--prob-threshold",
        type=float,
        default=0.5,
        help="predicted_themes if topic_probabilities_before_normalisation > this",
    )
    parser.add_argument("--max-text-chars", type=int, default=6000)
    parser.add_argument("--max-retries", type=int, default=5)
    parser.add_argument("--retry-delay", type=float, default=2.0)
    parser.add_argument("--test-limit", type=int, default=0)
    args = parser.parse_args()

    if args.preset == "tier2":
        default_in, default_out = str(tier2_input), str(tier2_output)
    else:
        default_in, default_out = str(tier1_input), str(tier1_output)
    if args.input is None:
        args.input = default_in
    if args.output is None:
        args.output = default_out

    try:
        resolved_map = resolve_default_mapping_path(tiered, args.preset, args.mapping)
    except FileNotFoundError as e:
        logger.error("%s", e)
        sys.exit(1)
    args.mapping = str(resolved_map)
    logger.info("Using category mapping: %s", resolved_map)

    asyncio.run(run_pipeline(args))


if __name__ == "__main__":
    main()
