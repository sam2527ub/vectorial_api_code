"""Repository for LinkedIn room pipeline async jobs (theme taxonomy, stimulus categorization)."""
import json
import logging
from typing import Any, Dict, List, Optional

from psycopg2.extras import Json

from app.database.connection import get_enterprise_audience_connection
from app.database.db_retry import db_retry_sync

logger = logging.getLogger(__name__)

JOB_TYPE_THEME = "theme_taxonomy"
JOB_TYPE_STIMULUS = "stimulus_categorization"
JOB_TYPE_GROUND_TRUTH = "ground_truth_extraction"
JOB_TYPE_INITIAL_PREDICTION = "linkedin_initial_prediction"
JOB_TYPE_LINKEDIN_SGO_PIPELINE = "linkedin_sgo_pipeline"

_STATUS_VALUES = ("PENDING", "PROCESSING", "COMPLETED", "FAILED")

# DDL/ALTER should not run on hot paths (status polling). We ensure on create/update.
_ensured_enterprises: set[str] = set()


def _normalize_result(value: Any) -> Optional[Dict[str, Any]]:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {"raw": value}
    return {"value": str(value)}


def ensure_linkedin_room_pipeline_job_table_exists(enterprise_name: Optional[str] = None) -> None:
    """Create LinkedInRoomPipelineJob if missing. Safe: idempotent."""
    key = (enterprise_name or "").strip().lower()
    if key in _ensured_enterprises:
        return
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS "LinkedInRoomPipelineJob" (
                    "id" TEXT NOT NULL,
                    "jobType" TEXT NOT NULL,
                    "status" TEXT NOT NULL DEFAULT 'PENDING',
                    "audienceRoomId" TEXT NOT NULL,
                    "model" TEXT,
                    "currentTier" TEXT,
                    "totalItems" INTEGER NOT NULL DEFAULT 0,
                    "completedItems" INTEGER NOT NULL DEFAULT 0,
                    "failedItems" INTEGER NOT NULL DEFAULT 0,
                    "failedPostIdsSample" JSONB,
                    "nextBatchIndex" INTEGER NOT NULL DEFAULT 0,
                    "result" JSONB,
                    "error" TEXT,
                    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    "updatedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT "LinkedInRoomPipelineJob_pkey" PRIMARY KEY ("id")
                )
            """)
        # Older deployments may already have the table without new columns. Add them safely.
        with conn.cursor() as cur:
            cur.execute('ALTER TABLE "LinkedInRoomPipelineJob" ADD COLUMN IF NOT EXISTS "currentTier" TEXT')
        with conn.cursor() as cur:
            cur.execute('ALTER TABLE "LinkedInRoomPipelineJob" ADD COLUMN IF NOT EXISTS "totalItems" INTEGER NOT NULL DEFAULT 0')
        with conn.cursor() as cur:
            cur.execute('ALTER TABLE "LinkedInRoomPipelineJob" ADD COLUMN IF NOT EXISTS "completedItems" INTEGER NOT NULL DEFAULT 0')
        with conn.cursor() as cur:
            cur.execute('ALTER TABLE "LinkedInRoomPipelineJob" ADD COLUMN IF NOT EXISTS "failedItems" INTEGER NOT NULL DEFAULT 0')
        with conn.cursor() as cur:
            cur.execute('ALTER TABLE "LinkedInRoomPipelineJob" ADD COLUMN IF NOT EXISTS "failedPostIdsSample" JSONB')
        with conn.cursor() as cur:
            cur.execute('ALTER TABLE "LinkedInRoomPipelineJob" ADD COLUMN IF NOT EXISTS "nextBatchIndex" INTEGER NOT NULL DEFAULT 0')
        with conn.cursor() as cur:
            cur.execute(
                'CREATE INDEX IF NOT EXISTS "LinkedInRoomPipelineJob_status_idx" '
                'ON "LinkedInRoomPipelineJob"("status")'
            )
        with conn.cursor() as cur:
            cur.execute(
                'CREATE INDEX IF NOT EXISTS "LinkedInRoomPipelineJob_room_idx" '
                'ON "LinkedInRoomPipelineJob"("audienceRoomId")'
            )
        with conn.cursor() as cur:
            cur.execute(
                'CREATE INDEX IF NOT EXISTS "LinkedInRoomPipelineJob_type_idx" '
                'ON "LinkedInRoomPipelineJob"("jobType")'
            )
    logger.info("LinkedInRoomPipelineJob table ensured to exist")
    _ensured_enterprises.add(key)


def create_linkedin_room_pipeline_job(
    job_id: str,
    job_type: str,
    audience_room_id: str,
    model: Optional[str] = None,
    enterprise_name: Optional[str] = None,
) -> Dict[str, Any]:
    if job_type not in (
        JOB_TYPE_THEME,
        JOB_TYPE_STIMULUS,
        JOB_TYPE_GROUND_TRUTH,
        JOB_TYPE_INITIAL_PREDICTION,
        JOB_TYPE_LINKEDIN_SGO_PIPELINE,
    ):
        raise ValueError(f"Invalid job_type: {job_type}")
    ensure_linkedin_room_pipeline_job_table_exists(enterprise_name)
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO "LinkedInRoomPipelineJob"
                (id, "jobType", status, "audienceRoomId", model, "createdAt", "updatedAt")
                VALUES (%s, %s, %s, %s, %s, NOW(), NOW())
                RETURNING id, "jobType", status, "audienceRoomId", model,
                  "currentTier", "totalItems", "completedItems", "failedItems", "failedPostIdsSample",
                  "nextBatchIndex",
                  result, error,
                  "createdAt", "updatedAt"
                """,
                (job_id, job_type, "PENDING", audience_room_id, model),
            )
            row = cur.fetchone()
        return _row_to_dict(row)


@db_retry_sync()
def get_linkedin_room_pipeline_job(
    job_id: str, enterprise_name: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    # Intentionally do NOT call ensure_* here: status polling should be a cheap read-only query.
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, "jobType", status, "audienceRoomId", model,
                  "currentTier", "totalItems", "completedItems", "failedItems", "failedPostIdsSample",
                  "nextBatchIndex",
                  result, error,
                  "createdAt", "updatedAt"
                FROM "LinkedInRoomPipelineJob"
                WHERE id = %s
                """,
                (job_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
        return _row_to_dict(row)


def _row_to_dict(row) -> Dict[str, Any]:
    # Backward/forward compatibility: tolerate older RETURNING shapes.
    # New shape (15 cols): id, jobType, status, audienceRoomId, model,
    # currentTier, totalItems, completedItems, failedItems, failedPostIdsSample, nextBatchIndex,
    # result, error, createdAt, updatedAt
    if len(row) == 9:
        return {
            "job_id": row[0],
            "job_type": row[1],
            "status": row[2],
            "audience_room_id": row[3],
            "model": row[4],
            "current_tier": None,
            "total_items": 0,
            "completed_items": 0,
            "failed_items": 0,
            "failed_post_ids_sample": None,
            "next_batch_index": 0,
            "result": _normalize_result(row[5]),
            "error": row[6],
            "created_at": row[7].isoformat() if row[7] else None,
            "updated_at": row[8].isoformat() if row[8] else None,
        }
    return {
        "job_id": row[0],
        "job_type": row[1],
        "status": row[2],
        "audience_room_id": row[3],
        "model": row[4],
        "current_tier": row[5],
        "total_items": row[6],
        "completed_items": row[7],
        "failed_items": row[8],
        "failed_post_ids_sample": row[9],
        "next_batch_index": row[10],
        "result": _normalize_result(row[11]),
        "error": row[12],
        "created_at": row[13].isoformat() if row[13] else None,
        "updated_at": row[14].isoformat() if row[14] else None,
    }


@db_retry_sync()
def update_linkedin_room_pipeline_job(
    job_id: str,
    enterprise_name: Optional[str] = None,
    **kwargs,
) -> None:
    ensure_linkedin_room_pipeline_job_table_exists(enterprise_name)
    # Never drop tier_mode on result replace (chunk completion paths used to set result without it).
    if "result" in kwargs and kwargs["result"] is not None and isinstance(kwargs["result"], dict):
        new_r = kwargs["result"]
        if "tier_mode" not in new_r or new_r.get("tier_mode") in (None, ""):
            existing = get_linkedin_room_pipeline_job(job_id, enterprise_name=enterprise_name)
            old_r = (existing or {}).get("result")
            if isinstance(old_r, dict) and old_r.get("tier_mode"):
                kwargs = {**kwargs, "result": {**new_r, "tier_mode": old_r["tier_mode"]}}
        merged_r = kwargs["result"]
        if isinstance(merged_r, dict) and (
            "tier" not in merged_r or merged_r.get("tier") in (None, "")
        ):
            existing = get_linkedin_room_pipeline_job(job_id, enterprise_name=enterprise_name)
            old_r = (existing or {}).get("result")
            if isinstance(old_r, dict) and old_r.get("tier") is not None:
                kwargs = {**kwargs, "result": {**merged_r, "tier": old_r["tier"]}}
    set_clauses: List[str] = ['"updatedAt" = NOW()']
    values: List[Any] = []
    field_mapping = {
        "status": "status",
        "current_tier": '"currentTier"',
        "total_items": '"totalItems"',
        "completed_items": '"completedItems"',
        "failed_items": '"failedItems"',
        "failed_post_ids_sample": '"failedPostIdsSample"',
        "next_batch_index": '"nextBatchIndex"',
        "result": "result",
        "error": "error",
    }
    for key, db_field in field_mapping.items():
        if key not in kwargs:
            continue
        val = kwargs[key]
        if key in ("result", "failed_post_ids_sample") and val is not None:
            set_clauses.append(f"{db_field} = %s")
            values.append(Json(val))
        elif key not in ("result", "failed_post_ids_sample"):
            set_clauses.append(f"{db_field} = %s")
            values.append(val)
    if "status" in kwargs and kwargs["status"] not in _STATUS_VALUES:
        raise ValueError(f"Invalid status: {kwargs['status']}")
    values.append(job_id)
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE "LinkedInRoomPipelineJob"
                SET {", ".join(set_clauses)}
                WHERE id = %s
                """,
                values,
            )
