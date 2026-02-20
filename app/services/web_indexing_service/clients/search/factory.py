"""Factory for search client (Parallel API)."""
from typing import Optional
from .interface import SearchClientInterface
from .parallel_client import ParallelSearchClient
from app.services.web_indexing_service.config import WebIndexingConfig


def get_search_client(config: Optional[WebIndexingConfig] = None) -> SearchClientInterface:
    """Return the Parallel API search client (people/LinkedIn)."""
    return ParallelSearchClient(config or WebIndexingConfig())
