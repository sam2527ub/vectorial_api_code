"""S3 utility functions."""
import json
import logging
from typing import Dict, Any, Optional
from fastapi import HTTPException
from app.config import s3_client, s3_bucket, s3_region

logger = logging.getLogger(__name__)


def get_audience_type_from_source(source: Optional[str] = None) -> str:
    """
    Determine audience type (linkedin-audience or reddit-audience) from source field.
    
    Args:
        source: Source string (e.g., "linkedin", "reddit", etc.)
    
    Returns:
        "linkedin-audience" or "reddit-audience"
    """
    if source:
        source_lower = source.lower().strip()
        if "reddit" in source_lower:
            return "reddit-audience"
    # Default to linkedin-audience
    return "linkedin-audience"


def ensure_enterprise_audience_folders_exist(enterprise_name: Optional[str] = None) -> None:
    """
    Ensure both linkedin-audience and reddit-audience folders exist for an enterprise.
    Creates empty folder markers if they don't exist.
    
    Args:
        enterprise_name: Enterprise name (gamma, app, entelligence, beta). If None, uses "default"
    """
    if not s3_client or not s3_bucket:
        logger.warning("S3 not configured, skipping folder creation")
        return
    
    # Normalize enterprise name
    if enterprise_name:
        normalized_enterprise = enterprise_name.lower().strip()
    else:
        normalized_enterprise = "default"
    
    # Create both folder types
    for audience_type in ["linkedin-audience", "reddit-audience"]:
        folder_key = f"{normalized_enterprise}/{audience_type}/"
        
        try:
            # Check if folder marker already exists
            try:
                s3_client.head_object(Bucket=s3_bucket, Key=folder_key)
                logger.info(f"Enterprise folder already exists: {folder_key}")
            except s3_client.exceptions.ClientError as e:
                if e.response['Error']['Code'] == '404':
                    # Folder doesn't exist, create it
                    logger.info(f"Creating enterprise folder marker: {folder_key}")
                    s3_client.put_object(
                        Bucket=s3_bucket,
                        Key=folder_key,
                        Body=b'',  # Empty body
                        ContentType="application/x-directory"
                    )
                    logger.info(f"Successfully created enterprise folder: {folder_key}")
                else:
                    raise
        except Exception as e:
            logger.error(f"Failed to ensure folder exists for {folder_key}: {e}")
            # Don't fail the request if folder creation fails


def initialize_all_enterprise_audience_folders():
    """
    Initialize all enterprise and audience type folders in S3.
    Creates empty folder markers for:
    - app/linkedin-audience/
    - app/reddit-audience/
    - gamma/linkedin-audience/
    - gamma/reddit-audience/
    - entelligence/linkedin-audience/
    - entelligence/reddit-audience/
    - beta/linkedin-audience/
    - beta/reddit-audience/
    - default/linkedin-audience/
    - default/reddit-audience/
    
    This can be called once at startup or manually to ensure all folders exist.
    """
    enterprises = ["app", "gamma", "entelligence", "beta", "default"]
    logger.info(f"Initializing enterprise and audience folders in S3 ({len(enterprises)} enterprises × 2 audience types)")
    
    for enterprise in enterprises:
        try:
            ensure_enterprise_audience_folders_exist(enterprise)
        except Exception as e:
            logger.error(f"Failed to initialize folders for enterprise {enterprise}: {e}")
    
    logger.info("Enterprise and audience folder initialization complete")


def get_s3_key_for_audience(room_id: str, path: str, enterprise_name: Optional[str] = None, source: Optional[str] = None) -> str:
    """
    Generate S3 key with enterprise-based folder structure.
    
    New Structure: {enterpriseName}/{audienceType}/{room_id}/{path}
    - {enterpriseName}/linkedin-audience/{room_id}/{path} for LinkedIn
    - {enterpriseName}/reddit-audience/{room_id}/{path} for Reddit
    
    Args:
        room_id: Audience room ID
        path: Path within the room folder (e.g., "description.json", "profiles/{profile_id}/profile.json")
        enterprise_name: Enterprise name (gamma, app, entelligence, beta). If None, uses "default"
        source: Source string to determine audience type (linkedin/reddit). If None, defaults to "linkedin-audience"
    
    Returns:
        S3 key string
    """
    # Normalize enterprise name (lowercase, strip whitespace)
    if enterprise_name:
        normalized_enterprise = enterprise_name.lower().strip()
    else:
        normalized_enterprise = "default"
    
    # Determine audience type from source
    audience_type = get_audience_type_from_source(source)
    
    # Build the S3 key: {enterpriseName}/{audienceType}/{room_id}/{path}
    return f"{normalized_enterprise}/{audience_type}/{room_id}/{path}"


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

