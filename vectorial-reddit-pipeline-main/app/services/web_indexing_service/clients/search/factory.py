# app/services/web_indexing_service/clients/search/factory.py
from .interface import SearchClientInterface
from .parallel_client import ParallelSearchClient
from app.services.web_indexing_service.config import WebIndexingConfig


def get_search_client() -> SearchClientInterface:
    """Creates and returns the appropriate client implementation."""
    config = WebIndexingConfig()
    return ParallelSearchClient(config)
