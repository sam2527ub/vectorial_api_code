"""Configuration for Subreddit Similarity Filter service."""
import os
from app.config import logger

# Rate limiting and retry configuration
MAX_RETRIES = 3
INITIAL_RETRY_DELAY = 2.0  # seconds
BATCH_DELAY = 0.5  # seconds between batches
BATCH_SIZE_DEFAULT = 100
MAX_MATCHED_SUBREDDITS_IN_PROMPT = 50
S3_STORAGE_THRESHOLD = 100

# Job / chunked processing
SUBREDDIT_SIMILARITY_FILTER_JOB_TABLE_NAME = "SubredditSimilarityFilter"
CHUNK_SIZE_SUBREDDITS = 200  # subreddits per HTTP process chunk

# Minimum on-topic activities per user after filtering (relaxed vs strict 3)
DEFAULT_MIN_ACTIVITIES = 2


class SubredditFilterConfig:
    """Configuration for subreddit similarity filtering."""

    def __init__(self):
        self.max_retries = MAX_RETRIES
        self.initial_retry_delay = INITIAL_RETRY_DELAY
        self.batch_delay = BATCH_DELAY
        self.batch_size_default = BATCH_SIZE_DEFAULT
        self.max_matched_subreddits_in_prompt = MAX_MATCHED_SUBREDDITS_IN_PROMPT
        self.s3_storage_threshold = S3_STORAGE_THRESHOLD
        self.groq_model = "llama-3.3-70b-versatile"
        self.groq_temperature = 0.2  # slightly relaxed for inclusive similarity judgments
        self.job_table_name = SUBREDDIT_SIMILARITY_FILTER_JOB_TABLE_NAME
        self.chunk_size_subreddits = CHUNK_SIZE_SUBREDDITS
