"""Configuration for Post Dimension Tagging service."""
import os
from app.config import logger

# Table name for the dimension tagging job table (created if not exists)
POST_DIMENSION_TAGGING_JOB_TABLE_NAME = "PostDimensionTagging"

# Chunked processing (profiles per chunk) for Vercel - self-trigger next chunk via HTTP
CHUNK_SIZE_PROFILES = 10

# Retry configuration (used at post level in AI client)
MAX_RETRIES = 2
INITIAL_RETRY_DELAY = 2.0  # seconds

# Parallel processing: single global limit (no batch layer)
MAX_CONCURRENT_REQUESTS = 12  # Lower to avoid OpenAI burst 429s via gateway

# OpenAI rate limits (for gpt-4o-mini)
OPENAI_RPM_LIMIT = 500  # Requests per minute
OPENAI_TPM_LIMIT = 2000000  # Tokens per minute (2M for tier 1)
OPENAI_RATE_LIMIT_WINDOW = 60  # seconds


class PostDimensionTaggingConfig:
    """Configuration for post dimension tagging."""
    
    def __init__(self):
        """Initialize configuration."""
        self.job_table_name = POST_DIMENSION_TAGGING_JOB_TABLE_NAME
        self.chunk_size_profiles = CHUNK_SIZE_PROFILES
        self.max_retries = MAX_RETRIES
        self.initial_retry_delay = INITIAL_RETRY_DELAY
        # Single global concurrency limit (no batch layer)
        self.max_concurrent_requests = MAX_CONCURRENT_REQUESTS
        # OpenAI model for dimension tagging (gpt-4o-mini supports logprobs)
        self.openai_model = "gpt-4o-mini"
        self.openai_temperature = 0.1
        # OpenAI rate limits
        self.openai_rpm_limit = OPENAI_RPM_LIMIT
        self.openai_tpm_limit = OPENAI_TPM_LIMIT
        self.openai_rate_limit_window = OPENAI_RATE_LIMIT_WINDOW
