"""Search client for Parallel API (web indexing / parallel search)."""
from .interface import SearchClientInterface
from .factory import get_search_client

__all__ = ["SearchClientInterface", "get_search_client"]
