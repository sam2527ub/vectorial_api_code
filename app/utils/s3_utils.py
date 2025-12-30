"""S3 utility functions."""
import json
import logging
from typing import Dict, Any, Optional
from fastapi import HTTPException
from app.config import s3_client, s3_bucket, s3_region

logger = logging.getLogger(__name__)


def upload_json_to_s3(key: str, data: Dict[str, Any]) -> str:
    """Upload JSON payload to S3 and return a public URL."""
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")
    try:
        s3_client.put_object(
            Bucket=s3_bucket,
            Key=key,
            Body=json.dumps(data),
            ContentType="application/json",
        )
        return f"https://{s3_bucket}.s3.{s3_region}.amazonaws.com/{key}"
    except Exception as e:
        logger.error(f"S3 upload failed for {key}: {e}")
        raise HTTPException(status_code=500, detail="Failed to upload to S3")


def extract_s3_key_from_url(s3_url: Optional[str]) -> Optional[str]:
    """Extract S3 key from a full S3 URL.
    
    Examples:
    - https://bucket.s3.region.amazonaws.com/path/to/file.json -> path/to/file.json
    - https://bucket.s3.amazonaws.com/path/to/file.json -> path/to/file.json
    """
    if not s3_url:
        return None
    try:
        from urllib.parse import urlparse
        parsed = urlparse(s3_url)
        # Remove leading slash from path
        key = parsed.path.lstrip('/')
        return key if key else None
    except Exception as e:
        logger.error(f"Failed to extract S3 key from URL {s3_url}: {e}")
        return None


def fetch_json_from_s3(key: str) -> Dict[str, Any]:
    """Fetch JSON data from S3 by key."""
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")
    try:
        response = s3_client.get_object(Bucket=s3_bucket, Key=key)
        content = response['Body'].read().decode('utf-8')
        return json.loads(content)
    except s3_client.exceptions.NoSuchKey as e:
        raise HTTPException(status_code=404, detail=f"S3 object not found: {key}")
    except Exception as e:
        logger.error(f"Failed to fetch JSON from S3 for key {key}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch data from S3: {str(e)}")

