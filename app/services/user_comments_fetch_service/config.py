"""Configuration for User Comments Fetch service (LinkedIn profile comments via Apify)."""
import os

from app.config import LINKEDIN_COMMENTS_ACTOR_ID

DEFAULT_MAX_ITEMS = 40
DEFAULT_POSTED_LIMIT = "any"
PROFILES_PER_BATCH = 20


class UserCommentsFetchConfig:
    """Configuration for comment scraping (harvestapi/linkedin-profile-comments)."""

    def __init__(self):
        self.actor_id = os.getenv("APIFY_LINKEDIN_COMMENTS_ACTOR_ID", LINKEDIN_COMMENTS_ACTOR_ID)
        self.default_max_items = DEFAULT_MAX_ITEMS
        self.default_posted_limit = DEFAULT_POSTED_LIMIT
        self.profiles_per_batch = PROFILES_PER_BATCH
