"""Configuration for user activity scraping."""
REDDIT_USER_ACTIVITY_SCRAPER_ACTOR_ID = "epctex/reddit-scraper"


class ScrapingConfig:
    """Configuration for user activity scraping."""
    
    def __init__(self):
        """Initialize configuration."""
        self.batch_size = 50  # Users per batch
        self.max_concurrent_runs = 3
        self.batch_start_delay = 5  # Seconds
        self.batch_delay_jitter = 3  # Seconds
        self.max_retries = 3
        self.initial_retry_delay = 10  # Seconds
        self.scraper_actor_id = REDDIT_USER_ACTIVITY_SCRAPER_ACTOR_ID
