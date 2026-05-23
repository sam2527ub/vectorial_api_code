"""Username extraction utilities - wrapper for shared utilities."""
from app.utils.username_extractor import (
    extract_username_from_user,
    extract_username_from_activity
)


class UsernameExtractor:
    """Extracts usernames from various data structures."""
    
    @staticmethod
    def extract_from_user(user):
        """Extract username from user object."""
        return extract_username_from_user(user)
    
    @staticmethod
    def extract_from_activity(activity):
        """Extract username from activity object."""
        return extract_username_from_activity(activity)
