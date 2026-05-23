"""Subreddit name processing utilities."""
import re
from typing import List


class SubredditProcessor:
    """Handles subreddit name normalization."""
    
    @staticmethod
    def normalize_subreddit_name(subreddit: str) -> str:
        """Normalize subreddit name from various formats."""
        subreddit = subreddit.strip()
        if not subreddit:
            return ""
        
        # If it's a full URL, extract subreddit name
        url_match = re.search(r'/r/([^/?]+)', subreddit)
        if url_match:
            return url_match.group(1)
        
        # If it's in r/name format
        if subreddit.startswith('r/') or subreddit.startswith('/r/'):
            name = subreddit.lstrip('/r/').split('/')[0].split('?')[0]
            return name if name else ""
        
        # Already just the subreddit name
        return subreddit
    
    @staticmethod
    def normalize_subreddits(subreddits: List[str]) -> List[str]:
        """Normalize a list of subreddit names."""
        normalized = []
        seen = set()
        
        for subreddit in subreddits:
            normalized_name = SubredditProcessor.normalize_subreddit_name(subreddit)
            if normalized_name and normalized_name not in seen:
                normalized.append(normalized_name)
                seen.add(normalized_name)
        
        return normalized
