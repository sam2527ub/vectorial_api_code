"""Web Indexing Service - parallel search for people/LinkedIn."""
from app.services.web_indexing_service.web_indexing_handler import WebIndexingHandler
from app.services.web_indexing_service.config import WebIndexingConfig

request_handler = WebIndexingHandler()

__all__ = [
    "WebIndexingHandler",
    "WebIndexingConfig",
    "request_handler",
]
