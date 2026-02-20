"""Normalize raw classification items to { label, score, allScores } and pad to expected count."""
from typing import Dict, List


def normalize_single_classification(
    classification: Dict,
    classifier_labels: List[str],
) -> Dict:
    """Normalize one raw classification: label, score, allScores."""
    label = classification.get("label", "")
    score = classification.get("score", 0.0)
    all_scores = classification.get("scores", {})

    if label not in classifier_labels:
        label_lower = label.lower()
        matched = next(
            (l for l in classifier_labels if l.lower() == label_lower),
            None,
        )
        label = matched or (classifier_labels[0] if classifier_labels else "Unknown")

    try:
        score = float(score)
        if score > 1.0:
            score = score / 100.0
        score = max(0.0, min(1.0, score))
    except (ValueError, TypeError):
        score = 0.5

    normalized_scores: Dict[str, float] = {}
    if isinstance(all_scores, dict) and all_scores:
        total = 0.0
        for l in classifier_labels:
            try:
                v = float(all_scores.get(l, 0))
                if v > 1.0:
                    v = v / 100.0
                normalized_scores[l] = round(max(0.0, min(1.0, v)), 2)
                total += normalized_scores[l]
            except (ValueError, TypeError):
                normalized_scores[l] = 0.0

        if total > 1.0:
            for l in classifier_labels:
                normalized_scores[l] = round(normalized_scores[l] / total, 2)
        elif total < 1.0 and total > 0:
            missing = [l for l in classifier_labels if normalized_scores[l] == 0.0]
            if missing:
                per = (1.0 - total) / len(missing)
                for l in missing:
                    normalized_scores[l] = round(per, 2)
            else:
                for l in classifier_labels:
                    normalized_scores[l] = round(normalized_scores[l] / total, 2)
        elif total == 0:
            rem = max(0.0, 1.0 - score)
            per = rem / max(1, len(classifier_labels) - 1) if len(classifier_labels) > 1 else 0.0
            for l in classifier_labels:
                normalized_scores[l] = round(score, 2) if l == label else round(per, 2)
    else:
        rem = max(0.0, 1.0 - score)
        per = rem / max(1, len(classifier_labels) - 1) if len(classifier_labels) > 1 else 0.0
        for l in classifier_labels:
            normalized_scores[l] = round(score, 2) if l == label else round(per, 2)

    return {
        "label": label,
        "score": round(score, 2),
        "allScores": normalized_scores,
    }


def normalize_and_pad(
    classifications: List[Dict],
    expected_count: int,
    classifier_labels: List[str],
) -> List[Dict]:
    """Normalize each item and pad to expected_count with defaults."""
    default_label = classifier_labels[0] if classifier_labels else "Unknown"
    default_scores = {l: 0.0 for l in classifier_labels}
    if default_label in default_scores:
        default_scores[default_label] = 0.5
    default_item = {"label": default_label, "score": 0.5, "allScores": default_scores}

    results = [
        normalize_single_classification(c, classifier_labels)
        for c in classifications
    ]
    while len(results) < expected_count:
        results.append(default_item.copy())
    return results[:expected_count]
