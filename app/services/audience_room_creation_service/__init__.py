"""Audience Room Creation service - Create Audience Room (POST /api/v1/audience-rooms)."""
from app.services.audience_room_creation_service.audience_room_creation_handler import (
    AudienceRoomCreationHandler,
)

request_handler = AudienceRoomCreationHandler()

__all__ = [
    "AudienceRoomCreationHandler",
    "request_handler",
]
