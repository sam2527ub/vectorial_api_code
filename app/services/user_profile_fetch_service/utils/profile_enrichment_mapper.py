"""Map Apify results back to original profiles and validate enriched profiles."""
from typing import Any, Dict, List, Optional, Tuple

from app.services.user_profile_fetch_service.apify_profile_service import extract_apify_profile_fields


def build_url_to_original_profile_map(profiles: List[Dict[str, Any]]) -> Tuple[List[str], Dict[str, Dict[str, Any]]]:
    """
    Extract LinkedIn URLs from profiles and build url -> original profile map.
    Returns (list of valid linkedin_urls, map of url -> original profile).
    """
    from .url_normalizer import normalize_linkedin_url

    linkedin_urls: List[str] = []
    url_to_profile: Dict[str, Dict[str, Any]] = {}
    for profile in profiles:
        linkedin_url = profile.get("linkedin_url", "")
        if linkedin_url and "linkedin.com" in linkedin_url:
            normalized = normalize_linkedin_url(linkedin_url)
            linkedin_urls.append(normalized)
            url_to_profile[normalized] = profile
    return linkedin_urls, url_to_profile


def build_apify_url_to_result_map(apify_results: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Build normalized URL -> Apify result map from flat list of Apify profile items."""
    apify_url_map: Dict[str, Dict[str, Any]] = {}
    for apify_profile in apify_results:
        profile_url = apify_profile.get("url", "") or apify_profile.get("linkedinUrl", "")
        if profile_url:
            normalized_url = profile_url.lower().rstrip("/")
            apify_url_map[normalized_url] = apify_profile
    return apify_url_map


def _find_apify_data_for_url(
    linkedin_url: str,
    apify_url_map: Dict[str, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Find matching Apify result for a LinkedIn URL (exact or partial match)."""
    normalized = linkedin_url.lower().rstrip("/")
    if normalized in apify_url_map:
        return apify_url_map[normalized]
    for apify_url, apify_profile in apify_url_map.items():
        if normalized in apify_url or apify_url in normalized:
            return apify_profile
    return None


def _promote_extracted_to_top_level(
    enriched: Dict[str, Any], extracted: Dict[str, Any]
) -> None:
    """Fill top-level profile fields from extracted (profile_info) when missing or null."""
    if not extracted:
        return
    # Map profile_info keys -> top-level keys expected by API / profile.json
    promotions = [
        ("fullName", "name"),
        ("jobTitle", "jobTitle"),
        ("currentCompany", "current_company"),
        ("companyIndustry", "industry"),
        ("currentLocation", "current_location"),
        ("totalYearsExperience", "total_years_experience"),
        ("education", "education"),
        ("headline", "headline"),
        ("about", "about"),
    ]
    for src_key, top_key in promotions:
        val = extracted.get(src_key)
        if val is None:
            continue
        if top_key == "education" and isinstance(val, list):
            # Keep as list for compatibility; consumers can stringify if needed
            pass
        current = enriched.get(top_key)
        if current is None or current == "":
            enriched[top_key] = val


def merge_apify_result_into_profile(
    original_profile: Dict[str, Any],
    linkedin_url: str,
    apify_url_map: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Merge Apify result into original profile; set profile_info, apify_result (full data), apify_enriched, and top-level fields."""
    apify_data = _find_apify_data_for_url(linkedin_url, apify_url_map)
    extracted = extract_apify_profile_fields(apify_data) if apify_data else {}
    enriched = {
        **original_profile,
        "profile_info": extracted,
        "apify_enriched": bool(extracted and extracted.get("fullName")),
    }
    # Attach full Apify LinkedIn Profile Scraper result so profile.json stores everything (experiences, connections, followers, etc.)
    if apify_data:
        enriched["apify_result"] = apify_data
    # Promote extracted fields to top level so jobTitle, current_company, headline, etc. are not null in profile.json
    _promote_extracted_to_top_level(enriched, extracted)
    return enriched


def is_valid_enriched_profile(profile: Dict[str, Any]) -> bool:
    """
    Return True if the profile was successfully enriched and has valid data.
    Requires apify_enriched and at least fullName + (headline or jobTitle or currentCompany).
    """
    if not profile.get("apify_enriched", False):
        return False
    profile_info = profile.get("profile_info", {}) or {}
    has_name = bool(profile_info.get("fullName"))
    has_headline = bool(profile_info.get("headline"))
    has_job = bool(profile_info.get("jobTitle"))
    has_company = bool(profile_info.get("currentCompany"))
    return has_name and (has_headline or has_job or has_company)
