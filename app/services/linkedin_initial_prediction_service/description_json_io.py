"""Read audience ``description.json`` for initial prediction (summary + qualitative trait seed)."""

from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_KEYWORDS_LINE_PREFIX = "Keywords:"


def load_tribe_description(description_path: Path) -> Tuple[str, str]:
    if not description_path.is_file():
        raise FileNotFoundError(f"description.json not found: {description_path}")
    data = json.loads(description_path.read_text(encoding="utf-8"))
    summary = (data.get("summary") or "").strip()
    if not summary:
        raise ValueError(
            f"Non-empty 'summary' is required in {description_path} (audience / tribe characteristics)."
        )
    room = (data.get("audience_room_id") or "").strip()
    return summary, room


def empty_qualitative_summary() -> Dict[str, Any]:
    return {
        "inherent_behavioral_traits": [],
        "latent_motivations": {
            "main": [],
            "validation_triggers": [],
            "friction_points": [],
        },
        "implicit_goals": [],
    }


def qualitative_summary_from_linkedin_traits(traits: Any) -> Optional[Dict[str, Any]]:
    """Map ``description.json`` ``traits`` into qualitative_summary-shaped JSON for i0 prompts."""
    if not isinstance(traits, list) or not traits:
        return None
    eq = empty_qualitative_summary()
    items: List[Dict[str, Any]] = []
    for block in traits:
        if not isinstance(block, dict):
            continue
        title = str(block.get("title") or "").strip()
        tags = block.get("keywordTags") or []
        descs = block.get("descriptions") or []
        tag_list: List[str] = []
        if isinstance(tags, list):
            tag_list = [str(t).strip() for t in tags if str(t).strip()]
        seg_list: List[str] = []
        if isinstance(descs, str):
            one = descs.strip()
            if one:
                seg_list = [one]
        elif isinstance(descs, list):
            seg_list = [str(d).strip() for d in descs if str(d).strip()]
        parts: List[str] = []
        if title:
            parts.append(title)
        if tag_list:
            parts.append(_KEYWORDS_LINE_PREFIX + " " + ", ".join(tag_list[:40]))
        for seg in seg_list:
            parts.append(seg)
        blob = "\n\n".join(parts).strip()
        if not blob:
            continue
        items.append(
            {
                "text": blob,
                "rationalization": "Seeded from LinkedIn audience-room description.json (traits block).",
                "confidence_score": 0.85,
                "linkedin_packaging": {
                    "title": title,
                    "keywordTags": tag_list,
                    "description_segments": list(seg_list),
                },
            }
        )
    if not items:
        return None
    out = copy.deepcopy(eq)
    out["inherent_behavioral_traits"] = items
    return out


def try_load_qualitative_from_description_json(description_path: Path) -> Dict[str, Any]:
    """Baseline persona JSON from ``description.json`` traits; empty schema if missing."""
    if not description_path.is_file():
        return empty_qualitative_summary()
    try:
        doc = json.loads(description_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("[initial_prediction] could not read traits seed %s: %s", description_path, e)
        return empty_qualitative_summary()
    seeded = qualitative_summary_from_linkedin_traits(doc.get("traits"))
    return seeded if seeded else empty_qualitative_summary()
