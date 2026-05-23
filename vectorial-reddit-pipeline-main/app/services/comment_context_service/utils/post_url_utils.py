"""Utilities for extracting and batching post URLs."""
from typing import List, Set, Dict, Any


def extract_unique_post_urls(comments: List[Dict[str, Any]]) -> Set[str]:
    """Extract unique post URLs from a list of comments."""
    post_urls = set()
    for comment in comments:
        post_url = comment.get("postUrl")
        if post_url and isinstance(post_url, str):
            post_url = post_url.rstrip("/")
            post_urls.add(post_url)
    return post_urls


def batch_post_urls(post_urls: List[str], batch_size: int) -> List[List[str]]:
    """Split list of post URLs into batches of specified size."""
    batches = []
    for i in range(0, len(post_urls), batch_size):
        batches.append(post_urls[i:i + batch_size])
    return batches
