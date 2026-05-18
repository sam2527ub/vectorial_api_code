"""Enumerate posts in a contextual stimulus that have ground-truth topic probabilities."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

PostTriple = Tuple[str, Dict[str, Any], Dict[str, Any]]


def iter_posts_with_ground_truth(
    payload: Dict[str, Any],
    *,
    tier_hint: str = "tier",
) -> List[PostTriple]:
    """
    All posts under ``results_by_user`` with non-empty ``topic_probabilities``.

    ``tier_hint`` is accepted for call-site compatibility; initial prediction does not branch on it.
    """
    _ = tier_hint
    out: List[PostTriple] = []
    by_user = payload.get("results_by_user") or {}
    for profile_id, bundle in by_user.items():
        if not isinstance(bundle, dict):
            continue
        for post in bundle.get("posts") or []:
            if not isinstance(post, dict) or not post.get("post_id"):
                continue
            tp = post.get("topic_probabilities")
            if not isinstance(tp, dict) or not tp:
                continue
            out.append((str(profile_id), bundle, post))
    return out
