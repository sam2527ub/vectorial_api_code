"""S3 put/get for background workers — raises :class:`LinkedInSGOPipelineError`, never ``HTTPException``."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from .linkedin_sgo_pipeline_async_errors import LinkedInSGOPipelineError

log = logging.getLogger(__name__)


def _public_url(key: str) -> str:
    from app.config import s3_bucket, s3_region

    return f"https://{s3_bucket}.s3.{s3_region}.amazonaws.com/{key}"


def upload_json_to_s3_worker(key: str, data: Dict[str, Any]) -> str:
    from app.config import s3_bucket, s3_client

    if not s3_client or not s3_bucket:
        raise LinkedInSGOPipelineError("S3 is not configured (bucket/client missing).")
    try:
        s3_client.put_object(
            Bucket=s3_bucket,
            Key=key,
            Body=json.dumps(data, ensure_ascii=False).encode("utf-8"),
            ContentType="application/json",
        )
        return _public_url(key)
    except Exception as e:
        log.error("[SGO_S3] upload_json failed %s: %s", key, e)
        raise LinkedInSGOPipelineError(f"S3 JSON upload failed for {key}: {e}") from e


def upload_text_to_s3_worker(key: str, text: str, content_type: str = "text/plain; charset=utf-8") -> str:
    from app.config import s3_bucket, s3_client

    if not s3_client or not s3_bucket:
        raise LinkedInSGOPipelineError("S3 is not configured (bucket/client missing).")
    try:
        s3_client.put_object(
            Bucket=s3_bucket,
            Key=key,
            Body=text.encode("utf-8"),
            ContentType=content_type,
        )
        return _public_url(key)
    except Exception as e:
        log.error("[SGO_S3] upload_text failed %s: %s", key, e)
        raise LinkedInSGOPipelineError(f"S3 text upload failed for {key}: {e}") from e


def try_fetch_text_from_s3(key: str) -> Optional[str]:
    from app.config import s3_bucket, s3_client

    if not s3_client or not s3_bucket:
        return None
    try:
        resp = s3_client.get_object(Bucket=s3_bucket, Key=key)
        body = resp["Body"].read()
        return body.decode("utf-8")
    except Exception as e:
        log.info("[SGO_S3] try_fetch_text miss %s: %s", key, e)
        return None
