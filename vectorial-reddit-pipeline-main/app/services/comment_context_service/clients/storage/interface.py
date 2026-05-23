"""Interface for storage clients."""
from abc import ABC, abstractmethod
from typing import Any, Dict


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
        """Return the storage region."""
        pass

    @abstractmethod
    def read_json(self, key: str) -> Dict[str, Any]:
        """Read JSON data from storage by key."""
        pass

    @abstractmethod
    def write_json(self, key: str, data: Dict[str, Any]) -> str:
        """Write JSON data to storage and return URL."""
        pass
