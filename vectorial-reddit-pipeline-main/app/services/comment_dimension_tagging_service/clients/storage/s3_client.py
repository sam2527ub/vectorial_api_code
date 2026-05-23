"""S3 implementation of StorageClientInterface for comment dimension tagging."""
import os
from typing import Optional, Any
import boto3
from botocore.config import Config
from app.config import logger
from app.utils.s3_utils import fetch_json_from_s3, upload_json_to_s3
from .interface import StorageClientInterface


class S3StorageClient(StorageClientInterface):
    """
    S3 implementation of StorageClientInterface for comment dimension tagging.
    
    Handles reading and writing JSON data to/from S3 buckets.
    """
    
    def __init__(self):
        """Initialize S3 client from environment variables."""
        self._bucket = (
            os.getenv("AUDIENCE_BUCKET_NAME")
            or os.getenv("VECTOR_BUCKET_NAME")
            or "audience-room-uploads"
        )
        self._region = os.getenv("AWS_REGION", "us-west-2")
        self._client: Optional[Any] = None
        self._initialize()
    
    def _initialize(self) -> None:
        """
        Initialize S3 client with boto3.
        
        Sets up the boto3 S3 client if bucket is configured.
        """
        try:
            if self._bucket:
                max_pool_connections = int(os.getenv("S3_MAX_POOL_CONNECTIONS", "50"))
                config = Config(
                    max_pool_connections=max_pool_connections,
                    retries={'max_attempts': 3, 'mode': 'adaptive'}
            )

                self._client = boto3.client("s3", region_name=self._region,config=config)
                logger.debug(
                    f"S3 client initialized successfully for comment dimension tagging "
                    f"(bucket: {self._bucket})"
                )
            else:
                logger.warning("S3 bucket not configured for comment dimension tagging")
                self._client = None
        except Exception as e:
            logger.error(f"Failed to initialize S3 client for comment dimension tagging: {e}")
            self._client = None
    
    def is_configured(self) -> bool:
        """
        Return True if S3 is properly configured.
        
        Returns:
            True if S3 client is initialized, False otherwise
        """
        return self._client is not None
    
    def get_raw_client(self) -> Any:
        """
        Return the underlying boto3 S3 client.
        
        Returns:
            boto3 S3 client instance
        """
        return self._client
    
    def get_bucket_name(self) -> str:
        """
        Return the S3 bucket name.
        
        Returns:
            Bucket name string
        """
        return self._bucket
    
    def get_region(self) -> str:
        """
        Return the S3 region.
        
        Returns:
            AWS region string
        """
        return self._region
    
    def read_json(self, key: str) -> Any:
        """
        Read JSON data from S3 by key.
        
        Args:
            key: S3 object key/path
            
        Returns:
            Parsed JSON data
            
        Raises:
            ValueError: If S3 is not configured
        """
        if not self.is_configured():
            raise ValueError("S3 is not configured")
        return fetch_json_from_s3(
            key,
            s3_client=self._client,
            s3_bucket=self._bucket
        )
    
    def write_json(self, key: str, data: Any) -> str:
        """
        Write JSON data to S3 and return URL.
        
        Args:
            key: S3 object key/path
            data: Data to write (will be serialized to JSON)
            
        Returns:
            S3 URL of the written object
            
        Raises:
            ValueError: If S3 is not configured
        """
        if not self.is_configured():
            raise ValueError("S3 is not configured")
        return upload_json_to_s3(
            key,
            data,
            s3_client=self._client,
            s3_bucket=self._bucket,
            s3_region=self._region
        )
