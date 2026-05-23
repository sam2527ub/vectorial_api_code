"""Database connection management - Connection pools and context managers only."""
import os
import logging
from typing import Optional, Dict
from contextlib import contextmanager

import psycopg2
from psycopg2.pool import ThreadedConnectionPool
from app.database.enterprise_database_registry import (
    get_enterprise_database_url,
    format_display_name
)

logger = logging.getLogger(__name__)

_pools: Dict[str, Optional[ThreadedConnectionPool]] = {}

_DEFAULT_POOL_SIZE = 5
_DEFAULT_CONNECT_TIMEOUT = 10


def _get_pool_size() -> int:
    return int(os.getenv("DATABASE_POOL_SIZE", str(_DEFAULT_POOL_SIZE)))


def _get_connect_timeout() -> int:
    return int(os.getenv("DATABASE_CONNECT_TIMEOUT", str(_DEFAULT_CONNECT_TIMEOUT)))


def _append_connect_options(dsn: str) -> str:
    """Append connect_timeout and options to DSN if not already present."""
    timeout = _get_connect_timeout()
    sep = "&" if "?" in dsn else "?"
    extras = []
    if "connect_timeout" not in dsn:
        extras.append(f"connect_timeout={timeout}")
    if "options" not in dsn:
        extras.append("options=-c%20statement_timeout%3D30000")
    if extras:
        dsn = dsn + sep + "&".join(extras)
    return dsn


def _create_pool(env_var: str, pool_name: str) -> Optional[ThreadedConnectionPool]:
    """Create a connection pool from environment variable."""
    database_url = os.getenv(env_var)
    if not database_url:
        return None

    pool_size = _get_pool_size()
    database_url = _append_connect_options(database_url)
    
    try:
        pool = ThreadedConnectionPool(1, pool_size, database_url)
        logger.info(f"{pool_name} database pool initialized (max {pool_size} connections)")
        return pool
    except Exception as e:
        logger.error(f"Failed to initialize {pool_name} database pool: {e}")
        return None


def _get_or_create_pool(pool_key: str, env_var: str, pool_name: str) -> Optional[ThreadedConnectionPool]:
    """Get or create a connection pool."""
    global _pools
    
    if pool_key not in _pools:
        _pools[pool_key] = _create_pool(env_var, pool_name)
    
    return _pools.get(pool_key)


def get_main_pool() -> Optional[ThreadedConnectionPool]:
    """Get or create the main database connection pool."""
    return _get_or_create_pool("main", "DATABASE_URL", "Main")


def get_audience_pool() -> Optional[ThreadedConnectionPool]:
    """Get or create the audience database connection pool."""
    return _get_or_create_pool("audience", "AUDIENCE_DATABASE_URL", "Audience")


def get_enterprise_pool(enterprise_name: str) -> Optional[ThreadedConnectionPool]:
    """Get or create enterprise database connection pool dynamically."""
    normalized = enterprise_name.lower().strip()
    
    if normalized not in _pools:
        database_url = get_enterprise_database_url(normalized)
        if database_url:
            try:
                pool_size = _get_pool_size()
                database_url = _append_connect_options(database_url)
                _pools[normalized] = ThreadedConnectionPool(1, pool_size, database_url)
                display_name = format_display_name(normalized)
                logger.info(f"{display_name} database pool initialized (max {pool_size} connections)")
            except Exception as e:
                logger.error(f"Failed to initialize {normalized} database pool: {e}")
                _pools[normalized] = None
        else:
            logger.warning(f"{normalized.upper()}_DATABASE_URL environment variable not set")
            _pools[normalized] = None
    
    return _pools.get(normalized)


def _safe_putconn(pool: ThreadedConnectionPool, conn) -> None:
    """Return connection to pool, closing it if it's in a bad state."""
    try:
        if conn.closed:
            pool.putconn(conn, close=True)
        else:
            pool.putconn(conn)
    except Exception:
        try:
            conn.close()
        except Exception:
            pass


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
        try:
            conn.rollback()
        except Exception:
            pass
        raise e
    finally:
        _safe_putconn(pool, conn)


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
        try:
            conn.rollback()
        except Exception:
            pass
        raise e
    finally:
        _safe_putconn(pool, conn)


@contextmanager
def get_enterprise_audience_connection(enterprise_name: Optional[str] = None):
    """Context manager for enterprise-specific audience database connections."""
    normalized = enterprise_name.lower().strip() if enterprise_name else None
    
    if normalized:
        pool = get_enterprise_pool(normalized)
        if not pool:
            display_name = format_display_name(normalized)
            env_var = f"{normalized.upper()}_DATABASE_URL"
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
        try:
            conn.rollback()
        except Exception:
            pass
        raise e
    finally:
        _safe_putconn(pool, conn)


def close_pools():
    """Close all connection pools."""
    global _pools
    
    pool_names = {
        "main": "Main",
        "audience": "Audience"
    }
    
    for pool_key, pool in list(_pools.items()):
        if pool:
            pool.closeall()
            display_name = pool_names.get(pool_key, format_display_name(pool_key))
            logger.info(f"{display_name} database pool closed")
    
    _pools.clear()
