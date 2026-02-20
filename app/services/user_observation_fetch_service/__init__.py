"""User Observation Fetch service - parallel post scraping (Trigger + Status)."""
from app.services.user_observation_fetch_service.user_observation_fetch_handler import (
    UserObservationFetchHandler,
)
from app.services.user_observation_fetch_service.config import UserObservationFetchConfig

request_handler = UserObservationFetchHandler()

__all__ = [
    "UserObservationFetchHandler",
    "UserObservationFetchConfig",
    "request_handler",
]
