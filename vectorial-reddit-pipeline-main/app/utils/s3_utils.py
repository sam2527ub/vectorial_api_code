"""S3 utility functions."""
import json
import logging
from typing import Dict, Any, Optional, List
from fastapi import HTTPException

# Import for backward compatibility (will be removed after migration)
try:
    from app.config import s3_client as _default_s3_client, s3_bucket as _default_s3_bucket, s3_region as _default_s3_region
except ImportError:
    _default_s3_client = None
    _default_s3_bucket = None
    _default_s3_region = None

logger = logging.getLogger(__name__)


def get_source_audience_path(source: Optional[str] = None) -> str:
    """Convert source value to audience path."""
    if not source:
        return "linkedin-audience"
    
    source_lower = source.lower().strip()
    if source_lower == "reddit":
        return "reddit-audience"
    else:
        return "linkedin-audience"


def get_audience_type_from_source(source: Optional[str] = None) -> str:
    """Determine audience type from source field. Returns linkedin-audience or reddit-audience."""
    if source:
        source_lower = source.lower().strip()
        if "reddit" in source_lower:
            return "reddit-audience"
    return "linkedin-audience"


def ensure_enterprise_audience_folders_exist(
    enterprise_name: Optional[str] = None,
    s3_client: Optional[object] = None,
    s3_bucket: Optional[str] = None
) -> None:
    """Ensure linkedin-audience and reddit-audience folders exist for an enterprise. Creates empty folder markers if needed."""
    client = s3_client or _default_s3_client
    bucket = s3_bucket or _default_s3_bucket
    
    if not client or not bucket:
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
                client.head_object(Bucket=bucket, Key=folder_key)
                logger.info(f"Enterprise folder already exists: {folder_key}")
            except client.exceptions.ClientError as e:
                if e.response['Error']['Code'] == '404':
                    logger.info(f"Creating enterprise folder marker: {folder_key}")
                    client.put_object(
                        Bucket=bucket,
                        Key=folder_key,
                        Body=b'',
                        ContentType="application/x-directory"
                    )
                    logger.info(f"Successfully created enterprise folder: {folder_key}")
                else:
                    raise
        except Exception as e:
            logger.error(f"Failed to ensure folder exists for {folder_key}: {e}")


def initialize_all_enterprise_audience_folders():
    """Initialize all enterprise and audience type folders in S3. Creates folder markers for app, gamma, entelligence, beta, and default."""
    enterprises = ["app", "gamma", "entelligence", "beta", "default"]
    logger.info(f"Initializing enterprise and audience folders in S3 ({len(enterprises)} enterprises × 2 audience types)")
    
    for enterprise in enterprises:
        try:
            ensure_enterprise_audience_folders_exist(enterprise)
        except Exception as e:
            logger.error(f"Failed to initialize folders for enterprise {enterprise}: {e}")
    
    logger.info("Enterprise and audience folder initialization complete")


def get_s3_key_for_audience(room_id: str, path: str, enterprise_name: Optional[str] = None, source: Optional[str] = None) -> str:
    """Generate S3 key with enterprise-based folder structure: {enterpriseName}/{source}-audience/{room_id}/{path}."""
    if enterprise_name:
        normalized_enterprise = enterprise_name.lower().strip()
    else:
        normalized_enterprise = "default"
    
    audience_type = get_audience_type_from_source(source)
    return f"{normalized_enterprise}/{audience_type}/{room_id}/{path}"


def upload_json_to_s3(
    key: str,
    data: Dict[str, Any],
    s3_client: Optional[object] = None,
    s3_bucket: Optional[str] = None,
    s3_region: Optional[str] = None
) -> str:
    """Upload JSON payload to S3 and return a public URL."""
    client = s3_client or _default_s3_client
    bucket = s3_bucket or _default_s3_bucket
    region = s3_region or _default_s3_region
    
    if not client or not bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")
    try:
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=json.dumps(data),
            ContentType="application/json",
        )
        return f"https://{bucket}.s3.{region}.amazonaws.com/{key}"
    except Exception as e:
        logger.error(f"S3 upload failed for {key}: {e}")
        raise HTTPException(status_code=500, detail="Failed to upload to S3")


def extract_s3_key_from_url(s3_url: Optional[str]) -> Optional[str]:
    """Extract S3 key from a full S3 URL."""
    if not s3_url:
        return None
    try:
        from urllib.parse import urlparse
        parsed = urlparse(s3_url)
        key = parsed.path.lstrip('/')
        return key if key else None
    except Exception as e:
        logger.error(f"Failed to extract S3 key from URL {s3_url}: {e}")
        return None


def fetch_json_from_s3(
    key: str,
    s3_client: Optional[object] = None,
    s3_bucket: Optional[str] = None
) -> Dict[str, Any]:
    """Fetch JSON data from S3 by key."""
    client = s3_client or _default_s3_client
    bucket = s3_bucket or _default_s3_bucket
    
    if not client or not bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")
    try:
        response = client.get_object(Bucket=bucket, Key=key)
        content = response['Body'].read().decode('utf-8')
        return json.loads(content)
    except client.exceptions.NoSuchKey as e:
        raise HTTPException(status_code=404, detail=f"S3 object not found: {key}")
    except Exception as e:
        logger.error(f"Failed to fetch JSON from S3 for key {key}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch data from S3: {str(e)}")


def list_s3_objects_with_prefix(
    prefix: str,
    s3_client: Optional[object] = None,
    s3_bucket: Optional[str] = None
) -> List[str]:
    """List S3 objects with a given prefix."""
    client = s3_client or _default_s3_client
    bucket = s3_bucket or _default_s3_bucket
    
    if not client or not bucket:
        raise HTTPException(
            status_code=503,
            detail=(
                "S3 is not configured; set AUDIENCE_BUCKET_NAME "
                "or VECTOR_BUCKET_NAME."
            ),
        )

    object_keys: List[str] = []

    try:
        paginator = client.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=bucket, Prefix=prefix)

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


def copy_s3_object(
    source_key: str,
    destination_key: str,
    s3_client: Optional[object] = None,
    s3_bucket: Optional[str] = None,
    s3_region: Optional[str] = None
) -> str:
    """Copy an S3 object from source_key to destination_key and return the public URL."""
    client = s3_client or _default_s3_client
    bucket = s3_bucket or _default_s3_bucket
    region = s3_region or _default_s3_region
    
    if not client or not bucket:
        raise HTTPException(
            status_code=503,
            detail=(
                "S3 is not configured; set AUDIENCE_BUCKET_NAME "
                "or VECTOR_BUCKET_NAME."
            ),
        )

    try:
        copy_source = {
            "Bucket": bucket,
            "Key": source_key,
        }

        client.copy_object(
            CopySource=copy_source,
            Bucket=bucket,
            Key=destination_key,
        )

        logger.info(
            "Copied S3 object from %s to %s",
            source_key,
            destination_key,
        )

        return (
            f"https://{bucket}.s3.{region}.amazonaws.com/"
            f"{destination_key}"
        )

    except client.exceptions.NoSuchKey:
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
    """Replace the enterprise name segment in an S3 URL."""
    if not s3_url:
        return s3_url

    old_enterprise = old_enterprise.lower().strip()
    new_enterprise = new_enterprise.lower().strip()

    return s3_url.replace(
        f"/{old_enterprise}/",
        f"/{new_enterprise}/",
    )






