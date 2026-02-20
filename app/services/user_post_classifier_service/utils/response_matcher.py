"""Match raw classifications to expected post count (by post_id or truncate/pad)."""
from typing import Dict, List

from app.config import logger


def match_classifications_to_posts(
    classifications: List[Dict],
    expected_count: int,
    classifier_labels: List[str],
) -> List[Dict]:
    """
    Match classifications to posts when count doesn't match.
    Uses post_id if available; otherwise truncates or pads.
    """
    has_post_ids = all(
        isinstance(c.get("post_id"), int)
        for c in classifications
        if isinstance(c, dict)
    )

    if has_post_ids:
        id_to_classification = {}
        for c in classifications:
            if isinstance(c, dict):
                post_id = c.get("post_id")
                if isinstance(post_id, int) and 1 <= post_id <= expected_count:
                    id_to_classification[post_id] = c

        default_label = classifier_labels[0] if classifier_labels else "Unknown"
        default_scores = {label: 0.0 for label in classifier_labels}
        if default_label in default_scores:
            default_scores[default_label] = 0.5

        matched = []
        for i in range(1, expected_count + 1):
            if i in id_to_classification:
                matched.append(id_to_classification[i])
            else:
                matched.append({
                    "label": default_label,
                    "score": 0.5,
                    "scores": default_scores,
                })
        logger.info(
            f"Matched {len(id_to_classification)} by post_id, "
            f"filled {expected_count - len(id_to_classification)} defaults"
        )
        return matched

    if len(classifications) > expected_count:
        logger.info(f"Truncating {len(classifications)} to {expected_count}")
        return classifications[:expected_count]

    result = list(classifications)
    default_label = classifier_labels[0] if classifier_labels else "Unknown"
    default_scores = {label: 0.0 for label in classifier_labels}
    if default_label in default_scores:
        default_scores[default_label] = 0.5
    while len(result) < expected_count:
        result.append({
            "label": default_label,
            "score": 0.5,
            "scores": default_scores,
        })
    logger.info(f"Padded {len(classifications)} to {expected_count} with defaults")
    return result
