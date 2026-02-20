"""Configuration for User Profile Fetch service (Apify batch enrichment)."""
import os

# Apify profile scraper actor (LinkedIn) - same as app.config default
PROFILE_SCRAPER_ACTOR_ID = os.getenv("APIFY_PROFILE_SCRAPER_ACTOR_ID", "2SyF0bVxmgGr8IVCZ")

# Batching: Apify recommends batching for large requests
MAX_PROFILES_PER_BATCH = 50
MAX_WAIT_TIME_PER_BATCH_SEC = 300  # 5 minutes per batch
WAIT_INTERVAL_SEC = 3  # Poll every 3 seconds


class UserProfileFetchConfig:
    """Configuration for user profile fetch / Apify enrichment."""

    def __init__(self):
        self.actor_id = PROFILE_SCRAPER_ACTOR_ID
        self.max_profiles_per_batch = MAX_PROFILES_PER_BATCH
        self.max_wait_time_per_batch_sec = MAX_WAIT_TIME_PER_BATCH_SEC
        self.wait_interval_sec = WAIT_INTERVAL_SEC
