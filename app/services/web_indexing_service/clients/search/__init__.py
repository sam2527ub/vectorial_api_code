"""Search clients for web indexing (Parallel API, Apify)."""
from .interface import SearchClientInterface
from .factory import get_search_client, Provider

__all__ = ["SearchClientInterface", "get_search_client", "Provider"]
