# app/services/audience_room_creation_service/clients/database/factory.py
from .interface import DatabaseClientInterface
from .postgres_client import PostgresDatabaseClient


def get_database_client() -> DatabaseClientInterface:
    # In future, this can switch on env vars:
    # e.g. DATABASE_PROVIDER=postgres|mysql|mongodb
    return PostgresDatabaseClient()
