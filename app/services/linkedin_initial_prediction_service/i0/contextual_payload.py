"""Load contextual LinkedIn JSON bundles (``results_by_user`` / ``users`` alias)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


def coalesce_contextual_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize ``users`` → ``results_by_user`` (same rules as :func:`load_contextual_payload`)."""
    rb = data.get("results_by_user")
    if isinstance(rb, dict):
        return data
    users = data.get("users")
    if isinstance(users, dict):
        out = dict(data)
        out["results_by_user"] = users
        return out
    raise ValueError("Expected top-level key 'results_by_user' or 'users' (object)")


def load_contextual_payload(path: Path) -> Dict[str, Any]:
    """Full document: room_id, source_file, results_by_user, ...

    Accepts ``users`` as an alias for ``results_by_user`` (some filtered bundles use that key).
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    return coalesce_contextual_payload(data)
