"""Enterprise registry - derives enterprises from environment variables."""
import logging
import os
from typing import Set, Optional
from functools import lru_cache

logger = logging.getLogger(__name__)

RESERVED_NAMES = {"audience", "database", "main", "databases"}


@lru_cache(maxsize=1)
def discover_enterprises() -> Set[str]:
    """Discover enterprises dynamically from environment variables."""
    enterprises: Set[str] = set()

    for key, value in os.environ.items():
        if not value:
            continue

        if not key.endswith("_DATABASE_URL"):
            continue

        name = key.replace("_DATABASE_URL", "").lower()

        if name in RESERVED_NAMES:
            continue

        enterprises.add(name)

    logger.info(f"Discovered {len(enterprises)} enterprises: {sorted(enterprises)}")
    return enterprises


def is_valid_enterprise(enterprise_name: str) -> bool:
    """Check if enterprise name is valid."""
    if not enterprise_name:
        return False

    normalized = enterprise_name.lower().strip()
    return normalized in discover_enterprises()


def get_enterprise_env_var(enterprise_name: str) -> str:
    """Returns the expected env var name for the enterprise database URL."""
    return f"{enterprise_name.upper()}_DATABASE_URL"


def get_enterprise_database_url(enterprise_name: str) -> Optional[str]:
    """Get enterprise database URL directly from environment."""
    env_var = get_enterprise_env_var(enterprise_name)
    database_url = os.getenv(env_var)
    
    if not database_url:
        logger.debug(f"Environment variable {env_var} not found for enterprise '{enterprise_name}'")
    
    return database_url


def format_display_name(enterprise_name: str) -> str:
    """Format enterprise name for display."""
    return enterprise_name.capitalize()
