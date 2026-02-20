"""Parse classifier config (labels, examples) and extract post texts from posts."""
import json
from typing import Any, Dict, List, Optional

from app.config import logger


def parse_labels(labels_raw: Any, job_id: str) -> List[str]:
    """Parse classifier labels from DB (list, JSON string, or dict)."""
    out: List[str] = []
    try:
        if isinstance(labels_raw, list):
            out = labels_raw
        elif isinstance(labels_raw, str):
            parsed = json.loads(labels_raw)
            out = parsed if isinstance(parsed, list) else [labels_raw]
        elif isinstance(labels_raw, dict):
            if "labels" in labels_raw and isinstance(labels_raw["labels"], list):
                out = labels_raw["labels"]
            else:
                out = list(labels_raw.keys()) if labels_raw else []
    except Exception as e:
        logger.warning(f"[ClassifierJob {job_id}] Error parsing labels: {e}")
    return [str(l) for l in out if l]


def parse_examples(examples_raw: Any) -> Optional[Any]:
    """Parse classifier examples (dict, JSON string, or list). Return None if invalid."""
    if not examples_raw:
        return None
    try:
        if isinstance(examples_raw, dict):
            return examples_raw
        if isinstance(examples_raw, str):
            return json.loads(examples_raw)
        if isinstance(examples_raw, list):
            return examples_raw
    except Exception:
        pass
    return None


def extract_post_texts(posts: List[Dict[str, Any]]) -> List[str]:
    """Extract text from each post (text, content, or description)."""
    texts = []
    for post in posts:
        t = post.get("text", "") or post.get("content", "") or post.get("description", "") or ""
        if t and str(t).strip():
            texts.append(str(t).strip())
    return texts
