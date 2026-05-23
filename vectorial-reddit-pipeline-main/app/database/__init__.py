"""Database module - connection management and health checks only."""
# Connection management
from app.database.database_connection_pool_manager import (
    get_main_connection,
    get_audience_connection,
    get_enterprise_audience_connection,
    close_pools
)

# Health checks
from app.database.database_health_checker import (
    is_main_db_available,
    is_audience_db_available,
    check_main_db_connection,
    check_audience_db_connection
)

# Models (for type hints and direct model usage)
from app.database.models import (
    AudienceRoom,
    AudienceProfile,
    SummaryJob,
    RedditWorkflowJob,
    ParallelSearchJob
)

__all__ = [
    # Connection management
    "get_main_connection",
    "get_audience_connection",
    "get_enterprise_audience_connection",
    "close_pools",
    # Health checks
    "is_main_db_available",
    "is_audience_db_available",
    "check_main_db_connection",
    "check_audience_db_connection",
    # Models
    "AudienceRoom",
    "AudienceProfile",
    "SummaryJob",
    "RedditWorkflowJob",
    "ParallelSearchJob",
]
