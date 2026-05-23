# app/services/audience_group_summarization_service/clients/storage/factory.py
from .interface import StorageClientInterface
from .s3_client import S3StorageClient


def get_storage_client() -> StorageClientInterface:
    return S3StorageClient()
