# app/services/user_profile_summarization_service/clients/database/interface.py
from abc import ABC, abstractmethod
from contextlib import contextmanager
from typing import Optional, Any


class DatabaseClientInterface(ABC):
    """Interface for database clients (e.g., PostgreSQL)."""

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True if the database client is properly configured."""
        pass

    @abstractmethod
    @contextmanager
    def get_connection(self, enterprise_name: Optional[str] = None):
        """Get a database connection context manager."""
        pass

    @abstractmethod
    def get_raw_connection_manager(self) -> Any:
        """Return the underlying connection manager function."""
        pass
