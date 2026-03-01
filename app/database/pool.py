"""
Database connection pools and context managers.
"""
import os
import logging
from typing import Optional, Dict
from contextlib import contextmanager

from psycopg2.pool import ThreadedConnectionPool

logger = logging.getLogger(__name__)

_main_pool: Optional[ThreadedConnectionPool] = None
_audience_pool: Optional[ThreadedConnectionPool] = None
_enterprise_pools: Dict[str, Optional[ThreadedConnectionPool]] = {}


def get_main_pool() -> Optional[ThreadedConnectionPool]:
    """Get or create the main database connection pool."""
    global _main_pool
    if _main_pool is None:
        database_url = os.getenv("DATABASE_URL")
        if database_url:
            try:
                _main_pool = ThreadedConnectionPool(1, 10, database_url)
                logger.info("Main database pool initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize main database pool: {e}")
    return _main_pool


def get_audience_pool() -> Optional[ThreadedConnectionPool]:
    """Get or create the audience database connection pool."""
    global _audience_pool
    if _audience_pool is None:
        database_url = os.getenv("AUDIENCE_DATABASE_URL")
        if database_url:
            try:
                _audience_pool = ThreadedConnectionPool(1, 10, database_url)
                logger.info("Audience database pool initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize audience database pool: {e}")
    return _audience_pool


def get_enterprise_pool(enterprise_name: str) -> Optional[ThreadedConnectionPool]:
    """Dynamically get or create pool for an enterprise."""
    from app.database.enterprise_registry import get_enterprise_env_var, format_display_name

    normalized = enterprise_name.lower().strip()
    if normalized in _enterprise_pools and _enterprise_pools[normalized] is not None:
        return _enterprise_pools[normalized]

    env_var = get_enterprise_env_var(normalized)
    database_url = os.getenv(env_var)
    if database_url:
        try:
            pool = ThreadedConnectionPool(1, 10, database_url)
            display_name = format_display_name(normalized)
            logger.info(f"{display_name} database pool initialized successfully")
            _enterprise_pools[normalized] = pool
            return pool
        except Exception as e:
            logger.error(f"Failed to initialize {normalized} database pool: {e}")
    return None


@contextmanager
def get_main_connection():
    """Context manager for main database connections."""
    pool = get_main_pool()
    if not pool:
        raise Exception("Main database pool not available. Please set DATABASE_URL.")
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        pool.putconn(conn)


@contextmanager
def get_audience_connection():
    """Context manager for audience database connections."""
    pool = get_audience_pool()
    if not pool:
        raise Exception("Audience database pool not available. Please set AUDIENCE_DATABASE_URL.")
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        pool.putconn(conn)


@contextmanager
def get_enterprise_audience_connection(enterprise_name: Optional[str] = None):
    """Context manager for enterprise-specific audience database connections."""
    from app.database.enterprise_registry import is_valid_enterprise, get_enterprise_env_var, format_display_name

    normalized_enterprise = enterprise_name.lower().strip() if enterprise_name else None
    pool = None

    if normalized_enterprise and is_valid_enterprise(normalized_enterprise):
        pool = get_enterprise_pool(normalized_enterprise)
        if not pool:
            env_var = get_enterprise_env_var(normalized_enterprise)
            display_name = format_display_name(normalized_enterprise)
            raise Exception(f"{display_name} database pool not available. Please set {env_var}.")
    else:
        pool = get_audience_pool()
        if not pool:
            raise Exception("Audience database pool not available. Please set AUDIENCE_DATABASE_URL.")

    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        pool.putconn(conn)


def close_pools():
    """Close all connection pools."""
    global _main_pool, _audience_pool, _enterprise_pools
    from app.database.enterprise_registry import format_display_name

    if _main_pool:
        _main_pool.closeall()
        _main_pool = None
        logger.info("Main database pool closed")
    if _audience_pool:
        _audience_pool.closeall()
        _audience_pool = None
        logger.info("Audience database pool closed")
    for name, pool in _enterprise_pools.items():
        if pool:
            pool.closeall()
            logger.info(f"{format_display_name(name)} database pool closed")
    _enterprise_pools.clear()
