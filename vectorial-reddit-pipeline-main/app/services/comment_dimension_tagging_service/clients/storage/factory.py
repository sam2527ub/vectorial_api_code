"""Factory for creating storage clients for comment dimension tagging."""
from .s3_client import S3StorageClient
from app.config import logger


def get_storage_client():
    """
    Get the appropriate storage client for comment dimension tagging.
    
    Currently returns S3 storage client if configured.
    Can be extended to support other storage providers in the future.
    
    Returns:
        StorageClientInterface instance if configured, None otherwise
    """
    try:
        client = S3StorageClient()
        if client.is_configured():
            logger.debug("Using S3 storage client for comment dimension tagging")
            return client
        else:
            logger.warning("S3 storage client not configured")
            return None
    except Exception as e:
        logger.error(f"Failed to create storage client: {e}")
        return None
