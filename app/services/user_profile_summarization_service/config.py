"""Configuration for User Profile Summarization service (async profile summaries)."""

CHUNK_SIZE = 10  # Profiles per chunk
CHUNKS_PER_API_CALL = 3  # Process 3 chunks per API call (30 profiles)
MAX_CONCURRENT = 2  # Concurrent OpenAI calls per chunk


class UserProfileSummarizationConfig:
    """Configuration for async profile summarization."""

    def __init__(self, chunk_size: int = CHUNK_SIZE, chunks_per_call: int = CHUNKS_PER_API_CALL, max_concurrent: int = MAX_CONCURRENT):
        self.chunk_size = chunk_size
        self.chunks_per_api_call = chunks_per_call
        self.max_concurrent = max_concurrent
