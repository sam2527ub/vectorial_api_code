"""Configuration for Web Indexing Service (parallel + Apify search for people/LinkedIn)."""
import os
from app.config import logger


class WebIndexingConfig:
    """Configuration for web indexing (Parallel API and/or Apify)."""

    def __init__(self):
        """Initialize configuration."""
        # Parallel API
        self.parallel_api_key = os.getenv("PARALLEL_API_KEY")
        self.parallel_api_base_url = os.getenv(
            "PARALLEL_API_BASE_URL",
            "https://api.parallel.ai/v1beta/findall",
        )
        self.parallel_api_timeout = 30.0
        self.default_entity_type = "people"
        self.default_model = os.getenv("PARALLEL_DEFAULT_MODEL", "core")
        self.default_match_limit = int(os.getenv("PARALLEL_DEFAULT_MATCH_LIMIT", "100"))
        self.max_match_limit = 1000
        self.min_match_limit = 1

        # Apify (LinkedIn Company Employees Scraper)
        self.apify_token = os.getenv("APIFY_TOKEN")
        self.apify_actor_id = os.getenv(
            "APIFY_ACTOR_ID",
            "harvestapi/linkedin-company-employees",
        )
        self.apify_api_base_url = os.getenv(
            "APIFY_API_BASE_URL",
            "https://api.apify.com/v2",
        )
        self.apify_run_timeout_seconds = min(
            int(os.getenv("APIFY_RUN_TIMEOUT_SECONDS", "7200")),  # default 2 hours
            7200,
        )

    def validate_dependencies(self) -> None:
        """Validate that required dependencies are available (Parallel)."""
        if not self.parallel_api_key:
            raise ValueError(
                "PARALLEL_API_KEY not configured. Please set PARALLEL_API_KEY environment variable."
            )

    def get_api_headers(self) -> dict:
        """Get API headers for Parallel API requests."""
        return {
            "x-api-key": self.parallel_api_key,
            "Content-Type": "application/json",
            "parallel-beta": "findall-2025-09-15",
        }

    def get_apify_headers(self) -> dict:
        """Get API headers for Apify API requests."""
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.apify_token}",
        }
