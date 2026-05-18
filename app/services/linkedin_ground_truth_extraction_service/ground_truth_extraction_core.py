"""Pure helpers: payload shape, softmax, merge topic labels into posts (no I/O)."""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_LP_FALLBACK = -10.0


def topics_mapping_from_dict(data: Dict[str, Any]) -> Dict[str, List[str]]:
    """Build category → topic list from theme-discovery style JSON (``categories`` array)."""
    topics_by_category: Dict[str, List[str]] = {}
    for row in data.get("categories", []) or []:
        if not isinstance(row, dict):
            continue
        cat = row.get("category")
        topics = row.get("topics") or []
        if not cat:
            continue
        topics_by_category[str(cat).strip()] = list(topics)
    if not topics_by_category:
        raise ValueError("No categories in mapping")
    return topics_by_category


def load_topics_mapping_from_path(path: Path) -> Dict[str, List[str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected object JSON: {path}")
    return topics_mapping_from_dict(data)


def softmax_over_logprob_yes(
    topics: List[str],
    topic_logprobs: Dict[str, Dict[str, float]],
) -> Dict[str, float]:
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


def normalize_contextual_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    """Accept ``users`` as alias for ``results_by_user``."""
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


def yes_no_logprobs_from_completion(logprobs_data: Any) -> Tuple[float, float, Optional[str], Optional[str]]:
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
    except Exception:
        return _LP_FALLBACK, _LP_FALLBACK, None, None

    if not math.isfinite(yes_lp):
        yes_lp = _LP_FALLBACK
    if not math.isfinite(no_lp):
        no_lp = _LP_FALLBACK

    return yes_lp, no_lp, yes_token, no_token


def normalize_yes_no_probs(logprob_yes: float, logprob_no: float) -> Tuple[float, float]:
    ly = float(logprob_yes) if math.isfinite(logprob_yes) else _LP_FALLBACK
    ln = float(logprob_no) if math.isfinite(logprob_no) else _LP_FALLBACK
    py = math.exp(ly)
    pn = math.exp(ln)
    total = py + pn
    if total > 0 and math.isfinite(total):
        return py / total, pn / total
    return 0.5, 0.5


def logprob_fallback() -> float:
    return _LP_FALLBACK


def deepcopy_payload(p: Dict[str, Any]) -> Dict[str, Any]:
    return copy.deepcopy(p)


def save_contextual_json(payload: Dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp.replace(path)
