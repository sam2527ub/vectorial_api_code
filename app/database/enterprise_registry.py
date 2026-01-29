"""Enterprise registry - auto-discovers enterprises from environment variables."""
import os
import re
import logging
from typing import Set, Optional
from threading import Lock

logger = logging.getLogger(__name__)

_registry_lock = Lock()
_discovered_enterprises: Optional[Set[str]] = None

# Reserved names that should not be treated as enterprises
RESERVED_NAMES = {'database', 'audience', 'main'}

def discover_enterprises_from_env() -> Set[str]:
    """Auto-discover enterprise names from environment variables (NAME_DATABASE_URL)."""
    enterprises = set()
    pattern = re.compile(r'^([A-Z_]+)_DATABASE_URL$')
    
    for env_key in os.environ.keys():
        match = pattern.match(env_key)
        if not match:
            continue
            
        enterprise_name = match.group(1).lower()
        if enterprise_name not in RESERVED_NAMES:
            enterprises.add(enterprise_name)
    
    logger.info(f"Discovered {len(enterprises)} enterprises: {sorted(enterprises)}")
    return enterprises

def get_all_enterprises() -> Set[str]:
    """Get all discovered enterprises (cached, thread-safe)."""
    global _discovered_enterprises
    
    with _registry_lock:
        if _discovered_enterprises is None:
            _discovered_enterprises = discover_enterprises_from_env()
        return _discovered_enterprises.copy()

def get_enterprise_env_var(enterprise_name: str) -> str:
    return f"{enterprise_name.upper()}_DATABASE_URL"

def format_display_name(enterprise_name: str) -> str:
    return enterprise_name.lower().strip().capitalize()

def is_valid_enterprise(enterprise_name: str) -> bool:
    if not enterprise_name:
        return False
    normalized = enterprise_name.lower().strip()
    return normalized in get_all_enterprises()