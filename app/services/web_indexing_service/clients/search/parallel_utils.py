"""Helpers for Parallel API responses (people/LinkedIn profiles)."""
from typing import Any, Dict, List
from app.config import logger


def extract_findall_id(run_data: Dict[str, Any]) -> str:
    """Extract findall_id from Parallel API start-run response."""
    findall_id = run_data.get("findall_id") or run_data.get("id") or run_data.get("run_id")
    if not findall_id:
        logger.error(f"No findall_id in response. Keys: {list(run_data.keys())}")
        raise ValueError(
            f"No findall_id returned from Parallel API. Response keys: {list(run_data.keys())}"
        )
    return str(findall_id)


def extract_candidates(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract candidates list from Parallel API result response."""
    candidates = data.get("candidates", [])
    if not isinstance(candidates, list):
        return []
    return candidates


def process_candidates_to_profiles(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Map Parallel API candidates to profile dicts (LinkedIn).
    Only includes matched candidates with linkedin.com URLs.
    """
    profiles = []
    for candidate in candidates:
        match_status = candidate.get("match_status", "")
        if match_status != "matched":
            continue
        linkedin_url = candidate.get("url", "")
        if not linkedin_url or "linkedin.com" not in linkedin_url:
            continue
        profile = {
            "linkedin_url": linkedin_url,
            "name": candidate.get("name", ""),
            "description": candidate.get("description", ""),
            "match_status": "matched",
            "output": candidate.get("output", {}),
            "basis": candidate.get("basis", []),
        }
        profiles.append(profile)
        logger.debug(f"Collected LinkedIn URL: {linkedin_url}")
    return profiles


def extract_run_status(run_data: Dict[str, Any]) -> str:
    """Extract run status string from Parallel API run response."""
    status_value = run_data.get("status", "")
    if isinstance(status_value, dict):
        run_status = (
            status_value.get("status")
            or status_value.get("state")
            or status_value.get("value")
            or ""
        )
        return str(run_status).upper() if run_status else ""
    if isinstance(status_value, str):
        return status_value.upper()
    return str(status_value).upper() if status_value else ""
