"""Configuration for comment context summary service."""
class CommentContextSummaryConfig:
    """Configuration for adding context + AI summary to comments via gateway."""

    def __init__(self):
        # Comments per profile limit
        self.comments_per_profile = 40
        
        # Model configuration
        self.groq_model = "llama-3.3-70b-versatile"
        self.groq_temperature = 0.1
        
        # Chunked processing (profiles per chunk)
        self.chunk_size_profiles = 10
        
        # Rate limiting (Groq limits - conservative defaults)
        self.groq_rpm_limit = 500  # Requests per minute
        self.groq_tpm_limit = 1000000  # Tokens per minute (1M)
        self.groq_rate_limit_window = 60  # seconds
        
        # Concurrency settings
        self.max_concurrent_api_calls = 30  # Concurrent API calls per chunk
        self.max_concurrent_run_loads = 30  # Concurrent S3 run file loads
        
        # Context building limits
        self.context_max_post_length = 2000
        self.context_max_parent_comment_length = 1000
