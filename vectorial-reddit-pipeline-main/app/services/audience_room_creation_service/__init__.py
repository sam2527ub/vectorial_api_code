"""Audience room creation service."""
from app.services.audience_room_creation_service.audience_room_creation_handler import AudienceRoomCreationHandler

request_handler = AudienceRoomCreationHandler()

__all__ = [
    "request_handler",
    "AudienceRoomCreationHandler",
]
