# Public re-exports for client factories (AI calls go via ai_gateway directly)
from .storage.factory import get_storage_client

__all__ = ["get_storage_client"]
