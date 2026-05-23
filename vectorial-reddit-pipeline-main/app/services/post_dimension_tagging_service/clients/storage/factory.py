"""Factory for creating storage clients."""
from .s3_client import S3StorageClient
from app.config import logger


def get_storage_client():
    """Get the appropriate storage client."""
    try:
        client = S3StorageClient()
        if client.is_configured():
            logger.debug("Using S3 storage client for post dimension tagging")
            return client
        else:
            logger.warning("S3 storage client not configured")
            return None
    except Exception as e:
        logger.error(f"Failed to create storage client: {e}")
        return None
