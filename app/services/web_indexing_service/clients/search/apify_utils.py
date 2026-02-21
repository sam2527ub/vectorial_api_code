"""Helpers for Apify LinkedIn Company Employees output → common profile shape."""
from typing import Any, Dict, List, Optional


def apify_item_to_common_profile(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Map one Apify profile object to the same shape as Parallel (common profile).
    Ensures linkedin_url, name, description, match_status, output, basis.
    """
    linkedin_url = item.get("linkedinUrl") or item.get("linkedin_url") or ""
    if not linkedin_url or "linkedin.com" not in linkedin_url:
        return None

    first = (item.get("firstName") or "").strip()
    last = (item.get("lastName") or "").strip()
    name = f"{first} {last}".strip() or item.get("id") or ""

    description = item.get("summary") or ""
    if not description and item.get("currentPositions"):
        pos = item["currentPositions"][0]
        title = pos.get("title") or ""
        company = pos.get("companyName") or ""
        if title or company:
            description = f"{title} at {company}".strip()

    return {
        "linkedin_url": linkedin_url,
        "name": name,
        "description": description,
        "match_status": "matched",
        "output": item,
        "basis": [],
    }


def apify_dataset_to_profiles(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Map a list of Apify profile items to common profile shape. Skips invalid items."""
    profiles = []
    for item in items:
        if not isinstance(item, dict):
            continue
        profile = apify_item_to_common_profile(item)
        if profile:
            profiles.append(profile)
    return profiles
