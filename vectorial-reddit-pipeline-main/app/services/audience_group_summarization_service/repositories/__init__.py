"""Database repository for audience group summarization service."""
from app.database.repositories.shared_repositories import (
    find_audience_room_by_id,
    update_audience_room
)

__all__ = [
    "find_audience_room_by_id",
    "update_audience_room"
]
