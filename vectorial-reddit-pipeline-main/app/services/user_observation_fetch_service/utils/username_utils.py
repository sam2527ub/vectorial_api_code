"""Username processing utilities."""
from typing import List, Set


class UsernameProcessor:
    """Handles username normalization and batching."""
    
    @staticmethod
    def normalize_username(username: str) -> str:
        """
        Clean username by removing u/ prefix.
        """
        username = username.strip()
        if username.startswith('u/'):
            username = username[2:]
        elif username.startswith('/u/'):
            username = username[3:]
        return username
    
    @staticmethod
    def normalize_usernames(usernames: List[str]) -> List[str]:
        """
        Normalize a list of usernames.
        """
        normalized = []
        for username in usernames:
            clean = UsernameProcessor.normalize_username(username)
            if clean:
                normalized.append(clean)
        return normalized
    
    @staticmethod
    def remove_duplicates(usernames: List[str]) -> List[str]:
        """
        Remove duplicates while preserving order.
        """
        seen: Set[str] = set()
        unique = []
        for u in usernames:
            if u not in seen:
                seen.add(u)
                unique.append(u)
        return unique
    
    @staticmethod
    def split_into_batches(items: List[str], batch_size: int) -> List[List[str]]:
        """
        Split list into batches of specified size.
        """
        batches = []
        for i in range(0, len(items), batch_size):
            batches.append(items[i:i + batch_size])
        return batches
    
    @staticmethod
    def convert_usernames_to_urls(usernames: List[str]) -> List[str]:
        """
        Convert usernames to Reddit profile URLs.
        """
        urls = []
        for username in usernames:
            clean_name = UsernameProcessor.normalize_username(username)
            if clean_name:
                urls.append(f"https://www.reddit.com/user/{clean_name}/")
        return urls
