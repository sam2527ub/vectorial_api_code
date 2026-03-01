"""Database health check utilities."""
import logging
from app.database.pool import (
    get_main_pool,
    get_audience_pool,
    get_main_connection,
    get_audience_connection,
)

logger = logging.getLogger(__name__)


def check_main_db_connection() -> bool:
    """Check if main database connection is available."""
    try:
        with get_main_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                return True
    except Exception as e:
        logger.error(f"Main database connection check failed: {e}")
        return False


def check_audience_db_connection() -> bool:
    """Check if audience database connection is available."""
    try:
        with get_audience_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                return True
    except Exception as e:
        logger.error(f"Audience database connection check failed: {e}")
        return False


def is_main_db_available() -> bool:
    """Check if main database pool is available."""
    return get_main_pool() is not None


def is_audience_db_available() -> bool:
    """Check if audience database pool is available."""
    return get_audience_pool() is not None
