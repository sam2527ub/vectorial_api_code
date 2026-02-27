"""Normalize LinkedIn profile URLs for matching."""
from typing import Optional


def normalize_profile_url(url: Optional[str]) -> Optional[str]:
    """Normalize a LinkedIn profile URL for consistent matching."""
    if not url or not isinstance(url, str):
        return None
    u = url.strip()
    if not u.startswith("http://") and not u.startswith("https://"):
        u = "https://" + u
    if u.startswith("https://linkedin.com") and "www." not in u[:25]:
        u = u.replace("https://linkedin.com", "https://www.linkedin.com")
    elif u.startswith("http://linkedin.com"):
        u = u.replace("http://linkedin.com", "https://www.linkedin.com")
    return u if "linkedin.com/in/" in u else None
