"""
Helpers for LinkedIn **i0** initial prediction: audience-room ``description.json`` traits text and
per-user profile excerpts from ``profiles/<profile_id>/profile.json``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Canonical **storage keys** for qualitative_summary lists (fixed schema — not “tier” labels).
# Human-facing subsection headings come from ``description.json`` ``traits[].title`` (copied onto
# each item as ``trait_section_title`` when seeding). These defaults apply only when that field is
# missing (e.g. legacy state or model-added traits without a title).
CANONICAL_QUALITATIVE_SUMMARY_KEYS: Tuple[str, ...] = (
    "skills_and_expertise",
    "working_style",
    "motivations_and_values",
    "pain_points_and_needs",
    "org_leadership_and_psychographic_profile",
)

DEFAULT_TRAIT_SECTION_TITLE_BY_KEY: Dict[str, str] = {
    "skills_and_expertise": "Skills & Expertise",
    "working_style": "Working Style",
    "motivations_and_values": "Motivations & Values",
    "pain_points_and_needs": "Pain Points & Needs",
    "org_leadership_and_psychographic_profile": "Organizational Leadership & Psychographic Profile",
}


def _trait_item_text_only(item: Any) -> str:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        return (item.get("text") or "").strip()
    return ""


def trait_section_heading_for_item(item: Any, canonical_key: str) -> str:
    """
    Subsection title for prompts / exports: prefer ``trait_section_title`` from the trait object
    (copied from ``description.json`` ``traits[].title`` when seeded), else default for ``canonical_key``.
    """
    if isinstance(item, dict):
        custom = (item.get("trait_section_title") or "").strip()
        if custom:
            return custom
    return DEFAULT_TRAIT_SECTION_TITLE_BY_KEY.get(
        canonical_key, canonical_key.replace("_", " ").title()
    )


def qualitative_summary_has_trait_descriptions(q: Any) -> bool:
    """True if ``qualitative_summary`` has at least one non-empty trait ``text`` in a canonical list."""
    if not isinstance(q, dict):
        return False
    for key in CANONICAL_QUALITATIVE_SUMMARY_KEYS:
        rows = q.get(key)
        if not isinstance(rows, list):
            continue
        for item in rows:
            if _trait_item_text_only(item):
                return True
    return False


def format_qualitative_summary_descriptions_only(q: Any) -> str:
    """
    Narrative trait context for prediction / memory / Part A: **only** each item's ``text``.
    Grouped under headings from ``trait_section_title`` when set (source: audience-room trait block
    ``title``); otherwise default labels per canonical key. Omits keywordTags, rationalizations, scores.
    """
    if not isinstance(q, dict):
        return "(No trait descriptions in persona state.)"
    sections: List[str] = []
    for key in CANONICAL_QUALITATIVE_SUMMARY_KEYS:
        rows = q.get(key)
        if not isinstance(rows, list) or not rows:
            continue
        # Preserve list order; open a new ## subsection whenever the display heading changes.
        chunks: List[Tuple[str, List[str]]] = []
        cur_h: Optional[str] = None
        cur_paras: List[str] = []
        for item in rows:
            t = _trait_item_text_only(item)
            if not t:
                continue
            h = trait_section_heading_for_item(item, key)
            if cur_h is None:
                cur_h = h
                cur_paras = [t]
            elif h == cur_h:
                cur_paras.append(t)
            else:
                chunks.append((cur_h, cur_paras))
                cur_h = h
                cur_paras = [t]
        if cur_h is not None:
            chunks.append((cur_h, cur_paras))
        for h, paras in chunks:
            body = "\n\n".join(paras)
            sections.append(f"## {h}\n\n{body}")
    if not sections:
        return "(No trait descriptions in persona state.)"
    return "\n\n".join(sections)


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
    Render ``description.json`` ``traits`` for LLM context: **description paragraphs only**, grouped
    by each block's ``title`` (audience-room subsection heading). Omits ``keywordTags``.
    The audience **group summary** is supplied separately (top-level ``summary``), not mixed in here.
    """
    if not isinstance(traits, list) or not traits:
        return "(No traits[] in description.json — use group summary only.)"
    sections: List[str] = []
    for block in traits:
        if not isinstance(block, dict):
            continue
        title = str(block.get("title") or "").strip()
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
            if title:
                sections.append(f"## {title}\n\n{blob}")
            else:
                sections.append(blob)
    if not sections:
        return "(Traits present but empty after parsing.)"
    return "\n\n".join(sections)


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
    Trait narrative for prompts: **descriptions only** (no keywordTags / scores / rationalizations).

    Prefer the current evolved ``qualitative_summary`` when it has trait ``text`` entries; otherwise
    seed from ``description.json`` ``traits[].descriptions`` (same narrative-only rule).
    """
    q = qualitative_summary if isinstance(qualitative_summary, dict) else {}
    if qualitative_summary_has_trait_descriptions(q):
        out = format_qualitative_summary_descriptions_only(q).strip()
        if out and not out.startswith("(No trait descriptions"):
            return out
    if not description_json_path or not description_json_path.is_file():
        return "(none)"
    txt = format_group_traits_text_from_description_path(description_json_path).strip()
    if not txt or txt.startswith("(Could not read"):
        return "(none)"
    return txt
