"""General helper functions."""
from fastapi import HTTPException
from app.database.database_health_checker import (
    is_main_db_available,
    is_audience_db_available
)


def ensure_db_available(db_type: str = "main") -> bool:
    """Check if database connection is available.
    
    Args:
        db_type: Either "main" or "audience"
    
    Returns:
        True if database is available, raises HTTPException otherwise
    """
    if db_type == "main":
        if not is_main_db_available():
            raise HTTPException(status_code=503, detail="Main database connection not available. Please set DATABASE_URL.")
        return True
    elif db_type == "audience":
        if not is_audience_db_available():
            raise HTTPException(status_code=503, detail="Audience database connection not available. Please set AUDIENCE_DATABASE_URL.")
        return True
    else:
        raise ValueError(f"Unknown db_type: {db_type}")






