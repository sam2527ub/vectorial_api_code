"""Factory for search clients (Parallel API, Apify)."""
from typing import Literal, Optional
from .interface import SearchClientInterface
from .parallel_client import ParallelSearchClient
from .apify_client import ApifySearchClient
from app.services.web_indexing_service.config import WebIndexingConfig

Provider = Literal["parallel", "apify"]


def get_search_client(
    provider: Provider = "parallel",
    config: Optional[WebIndexingConfig] = None,
) -> SearchClientInterface:
    """Return the search client for the given provider (parallel or apify)."""
    cfg = config or WebIndexingConfig()
    if provider == "apify":
        return ApifySearchClient(cfg)
    return ParallelSearchClient(cfg)
