# app/services/audience_room_creation_service/clients/database/postgres_client.py
from typing import Optional, Any
from contextlib import contextmanager
from app.config import logger
from app.database.database_connection_pool_manager import (
    get_enterprise_audience_connection,
    get_audience_pool,
)
from .interface import DatabaseClientInterface


class PostgresDatabaseClient(DatabaseClientInterface):
    """PostgreSQL implementation of DatabaseClientInterface."""
    
    def __init__(self):
        """Initialize PostgreSQL database client."""
        self._check_availability()
    
    def _check_availability(self) -> None:
        """Check if database is available."""
        try:
            # Try to get a pool to verify availability
            pool = get_audience_pool()
            if pool:
                logger.info("PostgreSQL database client initialized successfully for audience room creation")
            else:
                logger.warning("PostgreSQL database pool not available for audience room creation")
        except Exception as e:
            logger.error(f"Failed to initialize PostgreSQL database client: {e}")
    
    def is_configured(self) -> bool:
        """Check if database is configured."""
        try:
            pool = get_audience_pool()
            return pool is not None
        except Exception:
            return False
    
    @contextmanager
    def get_connection(self, enterprise_name: Optional[str] = None):
        """Get a database connection context manager."""
        with get_enterprise_audience_connection(enterprise_name) as conn:
            yield conn
    
    def get_raw_connection_manager(self) -> Any:
        """Return the underlying connection manager function."""
        return get_enterprise_audience_connection
