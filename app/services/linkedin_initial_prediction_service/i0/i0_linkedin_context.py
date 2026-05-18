"""
Helpers for LinkedIn **i0** initial prediction: audience-room ``description.json`` traits text and
per-user profile excerpts from ``profiles/<profile_id>/profile.json``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


def load_profile_json(profiles_root: Path, profile_id: str) -> Dict[str, Any]:
    """Load ``profiles/<profile_id>/profile.json`` for audience-room i0."""
    path = profiles_root / profile_id / "profile.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def load_description_json_doc(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def format_group_traits_from_description_traits(traits: Any) -> str:
    """
    Render ``description.json`` ``traits`` for LLM context: **description paragraphs only**.

    Omits ``title``, ``keywordTags``, and other metadata so prompts receive narrative trait text only.
    The audience **group summary** is supplied separately (top-level ``summary``), not mixed in here.
    """
    if not isinstance(traits, list) or not traits:
        return "(No traits[] in description.json — use group summary only.)"
    sections: List[str] = []
    for block in traits:
        if not isinstance(block, dict):
            continue
        descs = block.get("descriptions") or []
        lines: List[str] = []
        if isinstance(descs, str):
            d_one = descs.strip()
            if d_one:
                lines.append(d_one)
        elif isinstance(descs, list):
            for d in descs:
                ds = str(d).strip()
                if ds:
                    lines.append(ds)
        blob = "\n".join(lines).strip()
        if blob:
            sections.append(blob)
    if not sections:
        return "(Traits present but empty after parsing.)"
    return "\n\n---\n\n".join(sections)


def format_group_traits_text_from_description_path(description_path: Path) -> str:
    doc = load_description_json_doc(description_path)
    if not doc:
        return "(Could not read description.json for traits.)"
    return format_group_traits_from_description_traits(doc.get("traits"))


def extract_individual_profile_for_i0(card: Dict[str, Any], *, profile_id: str) -> str:
    """
    Individual “user profile” text for i0: prefer narrative ``summary`` on profile.json, then
    nested LinkedIn-style fields. ``profile_id`` must match the folder used to load ``card``.
    """
    blocks: List[str] = []
    blocks.append(f"User id (profiles folder key): {profile_id}")

    summary = (card.get("summary") or "").strip()
    if not summary:
        out = card.get("output")
        if isinstance(out, dict):
            summary = (out.get("summary") or "").strip()
    if not summary:
        summary = (card.get("about") or "").strip()
    if not summary:
        summary = (card.get("description") or "").strip()
    if not summary:
        ap = card.get("apify_result")
        if isinstance(ap, dict):
            summary = (ap.get("about") or "").strip()

    if summary:
        blocks.append("Individual profile summary (primary context):\n" + summary)
    headline = (card.get("headline") or card.get("jobTitle") or "").strip()
    if headline:
        blocks.append(f"Headline / role: {headline}")
    company = (card.get("current_company") or "").strip()
    if company:
        blocks.append(f"Current company: {company}")
    hl = card.get("highlights")
    if isinstance(hl, list) and hl:
        blocks.append("Highlights: " + "; ".join(str(x) for x in hl[:25]))
    kw = card.get("keywords")
    if isinstance(kw, list) and kw:
        blocks.append("Keywords: " + ", ".join(str(x) for x in kw[:45]))

    if len(blocks) <= 1:
        blocks.append(
            "[WARN] No summary/about text found in profile.json — check profiles/"
            f"{profile_id}/profile.json for `summary` or `output.summary`."
        )
    return "\n\n".join(blocks)


def resolve_group_traits_text(
    description_json_path: Optional[Path],
    qualitative_summary: Dict[str, Any],
) -> str:
    """
    Trait narrative text from ``description.json`` only.

    ``qualitative_summary`` is ignored (kept for call-site compatibility). There is no fallback from
    evolution state or inherent_behavioral_traits.
    """
    _ = qualitative_summary
    if not description_json_path or not description_json_path.is_file():
        return "(none)"
    txt = format_group_traits_text_from_description_path(description_json_path).strip()
    if not txt or txt.startswith("(Could not read"):
        return "(none)"
    return txt
