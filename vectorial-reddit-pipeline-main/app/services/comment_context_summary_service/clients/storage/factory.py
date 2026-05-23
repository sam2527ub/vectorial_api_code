"""Factory for storage clients."""
from .interface import StorageClientInterface
from .s3_client import S3StorageClient


def get_storage_client() -> StorageClientInterface:
    """Get storage client instance. Returns S3StorageClient by default."""
    return S3StorageClient()
