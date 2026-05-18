"""Theme divergence for i0 — logprobs (JSD) vs legacy confidence (recall)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .weighted_review_metrics import calculate_theme_delta, calculate_theme_delta_logprobs


def get_theme_delta(
    predicted_themes: Dict[str, float],
    actual_themes: Any,
    all_themes: Optional[List[str]] = None,
    *,
    scoring_mode: str = "logprobs",
) -> float:
    """
    Match legacy behaviour: ``logprobs`` modes use JSD on distributions; ``confidence`` uses recall.
    """
    sm = (scoring_mode or "confidence").strip().lower()
    if sm in ("logprobs", "logprobs-without-persona-context"):
        return float(calculate_theme_delta_logprobs(predicted_themes, actual_themes, all_themes))
    if isinstance(actual_themes, dict):
        rows = sorted(
            ((str(k), float(v)) for k, v in actual_themes.items()),
            key=lambda x: -x[1],
        )
        actual_list = [t for t, p in rows if p > 0.0]
        return float(calculate_theme_delta(predicted_themes, actual_list))
    return float(calculate_theme_delta(predicted_themes, actual_themes))
