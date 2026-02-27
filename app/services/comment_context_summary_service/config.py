"""Configuration for async LinkedIn comment context summary service."""


class CommentContextSummaryConfig:
    """Configuration for adding context summaries to LinkedIn comments.json."""

    def __init__(self) -> None:
        # Only enrich first N comments per profile
        self.comments_per_profile: int = 40

        # Chunking across profiles (per job)
        self.chunk_size_profiles: int = 10
        self.chunks_per_api_call: int = 3

        # Concurrency limits
        self.max_concurrent_profiles: int = 5
        self.max_concurrent_comments: int = 10

        # Groq / model config (used via AI Gateway + direct Groq fallback)
        self.groq_model: str = "groq/llama-3.3-70b-versatile"
        self.groq_temperature: float = 0.1

        # Context truncation limits
        self.context_max_post_length: int = 2000
        self.context_max_parent_comment_length: int = 1000

