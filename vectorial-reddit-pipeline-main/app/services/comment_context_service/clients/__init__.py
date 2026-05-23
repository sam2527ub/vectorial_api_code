"""Clients for comment context service."""
from .storage import get_storage_client
from .scraping import get_scraping_client

__all__ = ["get_storage_client", "get_scraping_client"]
