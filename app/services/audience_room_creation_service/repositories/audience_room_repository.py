"""Repository for creating audience rooms (wraps app.database)."""
from typing import Any, Dict, List, Optional

from app import database


def create_room(
    room_id: str,
    name: str,
    description_s3_url: str,
    user_id: Optional[str] = None,
    source: Optional[str] = None,
    query: Optional[str] = None,
    indexes_s3_url: Optional[str] = None,
    profiles_data: Optional[List[Dict[str, Any]]] = None,
    enterprise_name: Optional[str] = None,
):
    """Create AudienceRoom and profiles in the audience database. Returns the room (AudienceRoom)."""
    return database.create_audience_room(
        room_id=room_id,
        name=name,
        description_s3_url=description_s3_url,
        user_id=user_id,
        source=source,
        query=query,
        indexes_s3_url=indexes_s3_url,
        profiles_data=profiles_data,
        enterprise_name=enterprise_name,
    )
