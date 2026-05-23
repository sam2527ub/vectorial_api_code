"""S3 implementation of StorageClientInterface."""
import os
from typing import Optional, Any, Dict
import boto3
from app.config import logger
from app.utils.s3_utils import fetch_json_from_s3, upload_json_to_s3
from .interface import StorageClientInterface


class S3StorageClient(StorageClientInterface):
    """S3 implementation of StorageClientInterface."""

    def __init__(self):
        self._bucket = (
            os.getenv("AUDIENCE_BUCKET_NAME")
            or os.getenv("VECTOR_BUCKET_NAME")
            or "audience-room-uploads"
        )
        self._region = os.getenv("AWS_REGION", "us-west-2")
        self._client: Optional[Any] = None
        self._initialize()

    def _initialize(self) -> None:
        try:
            if self._bucket:
                self._client = boto3.client("s3", region_name=self._region)
                logger.info(
                    f"S3 client initialized successfully for comment context summary service "
                    f"(bucket: {self._bucket})"
                )
            else:
                logger.warning("S3 bucket not configured for comment context summary service")
                self._client = None
        except Exception as e:
            logger.error(f"Failed to initialize S3 client for comment context summary service: {e}")
            self._client = None

    def is_configured(self) -> bool:
        return self._client is not None

    def get_raw_client(self) -> Any:
        return self._client

    def get_bucket_name(self) -> str:
        return self._bucket

    def get_region(self) -> str:
        return self._region

    def read_json(self, key: str) -> Dict[str, Any]:
        if not self.is_configured():
            raise ValueError("S3 is not configured")
        return fetch_json_from_s3(
            key,
            s3_client=self._client,
            s3_bucket=self._bucket
        )

    def write_json(self, key: str, data: Dict[str, Any]) -> str:
        if not self.is_configured():
            raise ValueError("S3 is not configured")
        return upload_json_to_s3(
            key,
            data,
            s3_client=self._client,
            s3_bucket=self._bucket,
            s3_region=self._region
        )
