"""Normalize LinkedIn profile URLs for Apify and matching."""
from typing import List


def normalize_linkedin_url(url: str) -> str:
    """Normalize a single LinkedIn URL (add scheme, ensure www)."""
    url = (url or "").strip()
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url
    if url.startswith("https://linkedin.com") and not url.startswith("https://www.linkedin.com"):
        url = url.replace("https://linkedin.com", "https://www.linkedin.com")
    return url


def normalize_linkedin_urls(urls: List[str]) -> List[str]:
    """Normalize a list of LinkedIn URLs; only include valid linkedin.com URLs."""
    out = []
    for u in urls:
        n = normalize_linkedin_url(u)
        if n and "linkedin.com" in n:
            out.append(n)
    return out
