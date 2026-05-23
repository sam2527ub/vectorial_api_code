"""Configuration for comment context (scraping) service."""
REDDIT_SCRAPER_ACTOR_ID = "epctex/reddit-scraper"


class CommentContextServiceConfig:
    """Configuration for comment context scraping only."""

    def __init__(self):
        self.post_urls_per_batch = 100  # Post URLs per Apify run
        self.scraper_actor_id = REDDIT_SCRAPER_ACTOR_ID
        self.comments_per_profile = 40  # Used when collecting post URLs from comments
        # Cap concurrent Apify runs to stay under account memory limit (e.g. 131072 MB / 2048 MB per run)
        self.max_concurrent_apify_runs = 20
