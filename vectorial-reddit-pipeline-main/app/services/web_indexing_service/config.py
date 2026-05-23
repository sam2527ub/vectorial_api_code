"""Configuration for Web Indexing Service."""
import os
from typing import Optional
from app.config import logger


class WebIndexingConfig:
    """Configuration for web indexing/parallel scraping service."""
    
    def __init__(self):
        """Initialize configuration."""
        self.parallel_api_key = os.getenv("PARALLEL_API_KEY")
        self.parallel_api_base_url = os.getenv(
            "PARALLEL_API_BASE_URL",
            "https://api.parallel.ai/v1beta/findall"
        )
        self.parallel_api_timeout = 30.0
        self.default_model = os.getenv("PARALLEL_DEFAULT_MODEL", "core")
        self.default_match_limit = int(os.getenv("PARALLEL_DEFAULT_MATCH_LIMIT", "5"))
        self.max_match_limit = 1000
        self.min_match_limit = 1
    
    def validate_dependencies(self):
        """Validate that required dependencies are available."""
        if not self.parallel_api_key:
            raise ValueError(
                "PARALLEL_API_KEY not configured. Please set PARALLEL_API_KEY environment variable."
            )
    
    def get_api_headers(self) -> dict:
        """Get API headers for Parallel API requests."""
        return {
            "x-api-key": self.parallel_api_key,
            "Content-Type": "application/json",
            "parallel-beta": "findall-2025-09-15"
        }
