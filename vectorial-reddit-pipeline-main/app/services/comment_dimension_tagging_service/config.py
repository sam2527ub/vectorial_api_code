"""Configuration for Comment Dimension Tagging service."""
import os
from app.config import logger

# Table name for the dimension tagging job table
COMMENT_DIMENSION_TAGGING_JOB_TABLE_NAME = "CommentDimensionTagging"

# --- CHUNK SETTINGS ---
# Lowered from 15 to 10 to reduce the token load per Vercel invocation
CHUNK_SIZE_PROFILES = 10 
COMMENTS_PER_PROFILE = 40

# --- BATCH SETTINGS ---
# Keep at 8; this is the sweet spot for gpt-4o-mini performance
BATCH_SIZE_COMMENTS = 8  
MIN_BATCH_SIZE = 3  
MAX_BATCH_SIZE = 12  

# --- RETRY SETTINGS ---
# If you hit a 429, you need a long wait for the window to reset
MAX_RETRIES = 4            # Increased from 2
INITIAL_RETRY_DELAY = 15.0 # Increased from 2.0 (Mandatory for Tier 1)

# --- CONCURRENCY SETTINGS ---
# Reduced significantly to prevent "burst" 429 errors
MAX_CONCURRENT_BATCHES = 5   # Reduced from 20

MAX_CONCURRENT_REQUESTS = 30 

# Parallel chunk triggering - Set to 1 because we are now sequential
MAX_CONCURRENT_CHUNK_TRIGGERS = 1

# --- OPENAI LIMITS (Tier 1) ---
OPENAI_RPM_LIMIT = 500  
# Use a conservative 200k limit if that is your current tier
OPENAI_TPM_LIMIT = 2000000 
OPENAI_RATE_LIMIT_WINDOW = 60  

# --- TARGETS ---
TARGET_COMMENTS_PER_CHUNK = 320  
MIN_CHUNK_COMMENTS = 200  
MAX_CHUNK_COMMENTS = 400  
AVG_COMMENTS_PER_PROFILE = 40  

class CommentDimensionTaggingConfig:
    """Configuration for comment dimension tagging."""
    
    def __init__(self):
        """Initialize configuration."""
        self.job_table_name = COMMENT_DIMENSION_TAGGING_JOB_TABLE_NAME
        self.chunk_size_profiles = CHUNK_SIZE_PROFILES
        self.comments_per_profile = COMMENTS_PER_PROFILE
        self.batch_size_comments = BATCH_SIZE_COMMENTS
        self.min_batch_size = MIN_BATCH_SIZE
        self.max_batch_size = MAX_BATCH_SIZE
        self.max_retries = MAX_RETRIES
        self.initial_retry_delay = INITIAL_RETRY_DELAY
        self.max_concurrent_requests = MAX_CONCURRENT_REQUESTS
        self.max_concurrent_batches = MAX_CONCURRENT_BATCHES
        self.max_concurrent_chunk_triggers = MAX_CONCURRENT_CHUNK_TRIGGERS
        self.openai_model = "gpt-4o-mini"
        self.openai_temperature = 0.1
        self.openai_rpm_limit = OPENAI_RPM_LIMIT
        self.openai_tpm_limit = OPENAI_TPM_LIMIT
        self.openai_rate_limit_window = OPENAI_RATE_LIMIT_WINDOW
        self.target_comments_per_chunk = TARGET_COMMENTS_PER_CHUNK
        self.min_chunk_comments = MIN_CHUNK_COMMENTS
        self.max_chunk_comments = MAX_CHUNK_COMMENTS
        self.avg_comments_per_profile = AVG_COMMENTS_PER_PROFILE