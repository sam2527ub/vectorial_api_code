"""Configuration for subreddit user extraction."""


# Reddit Subreddit Members Scraper Actor
REDDIT_SUBREDDIT_MEMBERS_SCRAPER_ACTOR_ID = "louisdeconinck/reddit-subreddit-users"


class ExtractionConfig:
    """Configuration for subreddit user extraction."""
    
    def __init__(self):
        """Initialize configuration."""
        self.scraper_actor_id = REDDIT_SUBREDDIT_MEMBERS_SCRAPER_ACTOR_ID
        self.max_wait_time = 3600  # 1 hour max wait
        self.wait_interval = 5  # Check every 5 seconds
        self.max_users_per_subreddit = 100  # 100 users per subreddit (overridable per request)
        self.min_contribution = 2  # Minimum posts + comments for filtering
