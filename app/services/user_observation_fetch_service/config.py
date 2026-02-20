"""Configuration for User Observation Fetch service (parallel post scraping)."""
import os

POST_SCRAPER_ACTOR_ID = os.getenv("APIFY_POST_SCRAPER_ACTOR_ID", "curious_coder/linkedin-post-search-scraper")
MAX_URLS_PER_BATCH = 30
MAX_CONCURRENT_BATCHES = 3


class UserObservationFetchConfig:
    """Configuration for parallel scraping (post scraper)."""

    def __init__(self):
        self.post_scraper_actor_id = POST_SCRAPER_ACTOR_ID
        self.max_urls_per_batch = MAX_URLS_PER_BATCH
        self.max_concurrent_batches = MAX_CONCURRENT_BATCHES
