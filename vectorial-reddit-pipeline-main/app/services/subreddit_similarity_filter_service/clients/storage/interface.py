# app/services/subreddit_similarity_filter_service/clients/storage/interface.py
from abc import ABC, abstractmethod
from typing import Any


class StorageClientInterface(ABC):
    """Interface for storage clients (e.g., S3)."""

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True if the client is properly configured."""
        pass

    @abstractmethod
    def get_raw_client(self) -> Any:
        """Return the underlying SDK client."""
        pass

    @abstractmethod
    def get_bucket_name(self) -> str:
        """Return the bucket/container name."""
        pass

    @abstractmethod
    def get_region(self) -> str:
        """Return the storage region (if applicable)."""
        pass
