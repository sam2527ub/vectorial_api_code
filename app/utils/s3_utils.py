"""S3 utility functions."""
import json
import logging
from typing import Dict, Any, Optional, List
from fastapi import HTTPException
from app.config import s3_client, s3_bucket, s3_region
from app.database.enterprise_registry import get_all_enterprises

logger = logging.getLogger(__name__)


def get_source_audience_path(source: Optional[str] = None) -> str:
    """
    Convert source value to audience path.
    """
    if not source:
        return "linkedin-audience"  # Default to linkedin-audience
    
    source_lower = source.lower().strip()
    if source_lower == "reddit":
        return "reddit-audience"
    else:
        # Default to linkedin-audience for "LinkedIn", "Linkedin", or any other value
        return "linkedin-audience"
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
        enterprise_name: Enterprise name (auto-discover from env vars). If None, uses "default"
    """
    if not s3_client or not s3_bucket:
        logger.warning("S3 not configured, skipping folder creation")
        return

    if enterprise_name:
        normalized_enterprise = enterprise_name.lower().strip()
    else:
        normalized_enterprise = "default"

    for audience_type in ["linkedin-audience", "reddit-audience"]:
        folder_key = f"{normalized_enterprise}/{audience_type}/"
        try:
            try:
                s3_client.head_object(Bucket=s3_bucket, Key=folder_key)
                logger.info(f"Enterprise folder already exists: {folder_key}")
            except s3_client.exceptions.ClientError as e:
                if e.response["Error"]["Code"] == "404":
                    logger.info(f"Creating enterprise folder marker: {folder_key}")
                    s3_client.put_object(
                        Bucket=s3_bucket,
                        Key=folder_key,
                        Body=b"",
                        ContentType="application/x-directory",
                    )
                    logger.info(f"Successfully created enterprise folder: {folder_key}")
                else:
                    raise
        except Exception as e:
            logger.error(f"Failed to ensure folder exists for {folder_key}: {e}")


def initialize_all_enterprise_audience_folders():
    """
    Initialize all enterprise and audience type folders in S3.
    Creates empty folder markers for all discovered enterprises plus "default".
    For each enterprise, creates:
    - {enterprise}/linkedin-audience/
    - {enterprise}/reddit-audience/
    
    Enterprises are auto-discovered from environment variables matching pattern:
    {NAME}_DATABASE_URL
    """
    # Get all discovered enterprises dynamically
    enterprises = list(get_all_enterprises())
    enterprises.append("default")
    
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
    
    Structure: {enterpriseName}/{source}-audience/{room_id}/{path}
    
    Args:
        room_id: Audience room ID
        path: Path within the room folder (e.g., "description.json", "profiles/{profile_id}/profile.json")
        enterprise_name: Enterprise name (auto-discovered from env vars). If None, uses "default"
        source: Source value ("LinkedIn", "Reddit") to determine audience path. If None, defaults to "linkedin-audience"
    
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


def try_fetch_json_from_s3(key: str) -> Optional[Dict[str, Any]]:
    """
    Fetch JSON from S3; return None if the object is missing or S3 is not configured.
    Does not raise HTTPException (for batch jobs where missing profile files are normal).
    """
    from botocore.exceptions import ClientError

    if not s3_client or not s3_bucket:
        logger.warning("try_fetch_json_from_s3: S3 not configured for key %s", key)
        return None
    try:
        response = s3_client.get_object(Bucket=s3_bucket, Key=key)
        content = response["Body"].read().decode("utf-8")
        parsed: Dict[str, Any] = json.loads(content)
        return parsed if isinstance(parsed, dict) else None
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey", "NotFound"):
            return None
        logger.warning("try_fetch_json_from_s3: ClientError for key %s: %s", key, e)
        return None
    except Exception as e:
        logger.warning("try_fetch_json_from_s3: failed for key %s: %s", key, e)
        return None


from typing import List


def list_s3_objects_with_prefix(prefix: str) -> List[str]:
    if not s3_client or not s3_bucket:
        raise HTTPException(
            status_code=503,
            detail=(
                "S3 is not configured; set AUDIENCE_BUCKET_NAME "
                "or VECTOR_BUCKET_NAME."
            ),
        )

    object_keys: List[str] = []

    try:
        paginator = s3_client.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=s3_bucket, Prefix=prefix)

        for page in pages:
            contents = page.get("Contents", [])
            for obj in contents:
                object_keys.append(obj["Key"])

        logger.info(
            "Found %d S3 objects with prefix: %s",
            len(object_keys),
            prefix,
        )
        return object_keys

    except Exception as e:
        logger.error(
            "Failed to list S3 objects with prefix %s: %s",
            prefix,
            e,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to list S3 objects: {str(e)}",
        )


def copy_s3_object(source_key: str, destination_key: str) -> str:
    """
    Copy an S3 object from source_key to destination_key.
    """
    if not s3_client or not s3_bucket:
        raise HTTPException(
            status_code=503,
            detail=(
                "S3 is not configured; set AUDIENCE_BUCKET_NAME "
                "or VECTOR_BUCKET_NAME."
            ),
        )

    try:
        copy_source = {
            "Bucket": s3_bucket,
            "Key": source_key,
        }

        s3_client.copy_object(
            CopySource=copy_source,
            Bucket=s3_bucket,
            Key=destination_key,
        )

        logger.info(
            "Copied S3 object from %s to %s",
            source_key,
            destination_key,
        )

        return (
            f"https://{s3_bucket}.s3.{s3_region}.amazonaws.com/"
            f"{destination_key}"
        )

    except s3_client.exceptions.NoSuchKey:
        raise HTTPException(
            status_code=404,
            detail=f"Source S3 object not found: {source_key}",
        )

    except Exception as e:
        logger.error(
            "Failed to copy S3 object from %s to %s: %s",
            source_key,
            destination_key,
            e,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to copy S3 object: {str(e)}",
        )


def replace_enterprise_in_s3_url(
    s3_url: str,
    old_enterprise: str,
    new_enterprise: str,
) -> str:
    """
    Replace the enterprise name segment in an S3 URL.
    """
    if not s3_url:
        return s3_url

    old_enterprise = old_enterprise.lower().strip()
    new_enterprise = new_enterprise.lower().strip()

    return s3_url.replace(
        f"/{old_enterprise}/",
        f"/{new_enterprise}/",
    )