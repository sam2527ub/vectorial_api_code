"""Storage client interface."""
from abc import ABC, abstractmethod
from typing import Dict, Any, List


class StorageClientInterface(ABC):
    """Interface for storage clients."""
    
    @abstractmethod
    def read_json(self, key: str) -> Any:
        """Read JSON data from storage."""
        pass
    
    @abstractmethod
    def write_json(self, key: str, data: Any) -> str:
        """Write JSON data to storage and return URL."""
        pass
    
    @abstractmethod
    def is_configured(self) -> bool:
        """Check if storage is configured."""
        pass
