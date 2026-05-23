"""Storage client interface for comment dimension tagging."""
from abc import ABC, abstractmethod
from typing import Any


class StorageClientInterface(ABC):
    """
    Interface for storage clients used in comment dimension tagging.
    
    Provides methods for reading and writing JSON data to storage (e.g., S3).
    """
    
    @abstractmethod
    def read_json(self, key: str) -> Any:
        """
        Read JSON data from storage.
        
        Args:
            key: Storage key/path to read from
            
        Returns:
            Parsed JSON data (dict, list, etc.)
        """
        pass
    
    @abstractmethod
    def write_json(self, key: str, data: Any) -> str:
        """
        Write JSON data to storage and return URL.
        
        Args:
            key: Storage key/path to write to
            data: Data to write (will be serialized to JSON)
            
        Returns:
            URL of the written object
        """
        pass
    
    @abstractmethod
    def is_configured(self) -> bool:
        """
        Check if storage is configured and ready to use.
        
        Returns:
            True if storage is configured, False otherwise
        """
        pass
