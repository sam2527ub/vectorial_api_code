# app/services/user_observation_fetch_service/clients/storage/s3_client.py
import os
from typing import Optional, Any
import boto3
from app.config import logger
from .interface import StorageClientInterface


class S3StorageClient(StorageClientInterface):
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
                    f"S3 client initialized successfully for user observation fetch "
                    f"(bucket: {self._bucket})"
                )
            else:
                logger.warning("S3 bucket not configured for user observation fetch")
                self._client = None
        except Exception as e:
            logger.error(f"Failed to initialize S3 client for user observation fetch: {e}")
            self._client = None

    def is_configured(self) -> bool:
        return self._client is not None

    def get_raw_client(self) -> Any:
        return self._client

    def get_bucket_name(self) -> str:
        return self._bucket

    def get_region(self) -> str:
        return self._region

