# app/services/web_indexing_service/clients/search/parallel_utils.py
from typing import Any, Dict, List
from app.config import logger


def extract_findall_id(run_data: Dict[str, Any]) -> str:
    """Extract findall_id from API response."""
    findall_id = run_data.get("findall_id")
    
    if not findall_id:
        logger.error(f"No findall_id found in response. Keys: {list(run_data.keys())}")
        raise ValueError(f"No findall_id returned from Parallel API. Response keys: {list(run_data.keys())}")
    
    return findall_id


def _extract_subreddit_url(candidate: Dict[str, Any]) -> str:
    """Extract URL from candidate. Internal helper."""
    return candidate.get("url", "")


def _build_subreddit_dict(candidate: Dict[str, Any]) -> Dict[str, str]:
    """Build standardized subreddit dictionary from candidate. Internal helper."""
    # Extract reasoning from basis array (first basis item's reasoning, or empty)
    reasoning = ""
    basis = candidate.get("basis", [])
    if basis and isinstance(basis, list) and len(basis) > 0:
        first_basis = basis[0] if isinstance(basis[0], dict) else {}
        reasoning = first_basis.get("reasoning", "")
    
    return {
        "url": _extract_subreddit_url(candidate),
        "summary": candidate.get("description", ""),
        "reasoning": reasoning,
        "status": candidate.get("match_status", "matched")
    }


def extract_candidates(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract candidates list from API response."""
    candidates = data.get("candidates", [])
    if not isinstance(candidates, list):
        return []
    return candidates


def _filter_matched_candidates(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Filter candidates to only include those with match_status='matched'. Internal helper."""
    matched = [c for c in candidates if c.get("match_status") == "matched"]
    if len(matched) < len(candidates):
        logger.debug(f"Filtered {len(candidates)} candidates to {len(matched)} matched")
    return matched


def process_candidates(candidates: List[Dict[str, Any]], require_match: bool = True) -> List[Dict[str, Any]]:
    """Process candidates into subreddit dictionaries."""
    if require_match:
        candidates = _filter_matched_candidates(candidates)
    
    subreddits = []
    for candidate in candidates:
        subreddit_dict = _build_subreddit_dict(candidate)
        if subreddit_dict["url"]:
            subreddits.append(subreddit_dict)
        else:
            logger.warning(f"Candidate missing URL. Keys: {list(candidate.keys())}")
    
    return subreddits


def log_metrics_warning(data: Dict[str, Any], source: str = "") -> None:
    """Log warning if metrics indicate no matches found."""
    status_info = data.get("status", {})
    if isinstance(status_info, dict):
        metrics = status_info.get("metrics", {})
        generated = metrics.get("generated_candidates_count", 0)
        matched = metrics.get("matched_candidates_count", 0)
        
        if generated > 0 and matched == 0:
            reason = status_info.get("termination_reason", "unknown")
            logger.warning(
                f"{source}Parallel API generated {generated} candidates but 0 matched. "
                f"Termination reason: {reason}"
            )
