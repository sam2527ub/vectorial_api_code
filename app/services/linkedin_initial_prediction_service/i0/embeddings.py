"""OpenAI embedding helper with in-memory cache (used for text_delta cosine distance)."""

from __future__ import annotations

import logging
import time
from threading import Lock
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def get_or_compute_embedding(
    text: str,
    item_id: str,
    embedding_type: str,
    client: Any,
    cache: Dict[str, Any],
    *,
    embedding_model: str,
    min_interval_sec: float,
    lock: Optional[Lock] = None,
) -> Optional[List[float]]:
    """Compute embedding or return cached. ``client`` is sync ``OpenAI``."""
    key = f"{item_id}_{embedding_type}"
    if lock:
        with lock:
            if key in cache:
                return cache[key]
    elif key in cache:
        return cache[key]

    sanitized = str(text).replace("\n", " ")
    time.sleep(max(0.0, float(min_interval_sec)))

    try:
        response = client.embeddings.create(input=[sanitized], model=embedding_model)
        embedding = response.data[0].embedding
        if lock:
            with lock:
                cache[key] = embedding
        else:
            cache[key] = embedding
        return embedding
    except Exception as e:
        logger.warning("[initial_prediction.i0] embedding failed %s: %s", key, e)
        return None
