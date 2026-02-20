"""Run classification in batches (multiple posts per API call, multiple batches)."""
import asyncio
from typing import Any, Dict, List, Optional

from app.config import logger

from .clients.llm import get_classifier_client
from .utils.parser_helpers import extract_post_texts


async def classify_posts_batch(
    posts: List[Dict[str, Any]],
    classifier_name: str,
    classifier_prompt: str,
    classifier_description: str,
    classifier_labels: List[str],
    classifier_examples: Optional[Any] = None,
    batch_size: int = 20,
) -> List[Dict[str, Any]]:
    """
    Classify multiple posts in batches. Each batch is one API call.
    Uses the configured classifier client (e.g. Groq).
    """
    client = get_classifier_client()
    if not client or not client.is_configured():
        raise RuntimeError("Classifier client not configured")

    posts_texts = extract_post_texts(posts)
    if not posts_texts:
        return []

    results: List[Dict[str, Any]] = []
    default_label = classifier_labels[0] if classifier_labels else "Unknown"
    default_scores = {l: 0.0 for l in classifier_labels}
    if default_label in default_scores:
        default_scores[default_label] = 0.5
    default_item = {"label": default_label, "score": 0.5, "allScores": default_scores}

    for i in range(0, len(posts_texts), batch_size):
        batch_texts = posts_texts[i : i + batch_size]
        batch_num = (i // batch_size) + 1
        total_batches = (len(posts_texts) + batch_size - 1) // batch_size
        logger.info(f"Classifier batch {batch_num}/{total_batches} ({len(batch_texts)} posts)")

        try:
            batch_results = await client.classify_posts_single_call(
                posts_texts=batch_texts,
                classifier_name=classifier_name,
                classifier_prompt=classifier_prompt,
                classifier_description=classifier_description,
                classifier_labels=classifier_labels,
                classifier_examples=classifier_examples,
            )
            results.extend(batch_results)
        except Exception as e:
            logger.error(f"Classifier batch {batch_num} error: {e}")
            for _ in batch_texts:
                results.append(default_item.copy())

        if i + batch_size < len(posts_texts):
            await asyncio.sleep(1.0)

    return results
