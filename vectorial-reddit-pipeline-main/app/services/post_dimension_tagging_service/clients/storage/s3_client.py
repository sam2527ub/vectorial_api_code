"""S3 implementation of StorageClientInterface."""
import os
from typing import Optional, Any, Dict, List
import boto3
from app.config import logger
from app.utils.s3_utils import fetch_json_from_s3, upload_json_to_s3
from .interface import StorageClientInterface


class S3StorageClient(StorageClientInterface):
    """S3 implementation of StorageClientInterface."""

    def __init__(self):
        """Initialize S3 client."""
        self._bucket = (
            os.getenv("AUDIENCE_BUCKET_NAME")
            or os.getenv("VECTOR_BUCKET_NAME")
            or "audience-room-uploads"
        )
        self._region = os.getenv("AWS_REGION", "us-west-2")
        self._client: Optional[Any] = None
        self._initialize()

    def _initialize(self) -> None:
        """Initialize S3 client."""
        try:
            if self._bucket:
                self._client = boto3.client("s3", region_name=self._region)
                logger.debug(
                    f"S3 client initialized successfully for post dimension tagging "
                    f"(bucket: {self._bucket})"
                )
            else:
                logger.warning("S3 bucket not configured for post dimension tagging")
                self._client = None
        except Exception as e:
            logger.error(f"Failed to initialize S3 client for post dimension tagging: {e}")
            self._client = None

    def is_configured(self) -> bool:
        """Return True if S3 is properly configured."""
        return self._client is not None

    def get_raw_client(self) -> Any:
        """Return the underlying S3 client."""
        return self._client

    def get_bucket_name(self) -> str:
        """Return the S3 bucket name."""
        return self._bucket

    def get_region(self) -> str:
        """Return the S3 region."""
        return self._region

    def read_json(self, key: str) -> Dict[str, Any]:
        """Read JSON data from S3 by key."""
        if not self.is_configured():
            raise ValueError("S3 is not configured")
        return fetch_json_from_s3(
            key,
            s3_client=self._client,
            s3_bucket=self._bucket
        )

    def write_json(self, key: str, data: Any) -> str:
        """Write JSON data to S3 and return URL."""
        if not self.is_configured():
            raise ValueError("S3 is not configured")
        return upload_json_to_s3(
            key,
            data,
            s3_client=self._client,
            s3_bucket=self._bucket,
            s3_region=self._region
        )
