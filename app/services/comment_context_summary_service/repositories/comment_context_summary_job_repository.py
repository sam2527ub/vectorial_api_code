"""CommentContextSummaryJob repository for async comment context summary service."""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.database.connection import get_enterprise_audience_connection

logger = logging.getLogger(__name__)

COMMENT_CONTEXT_SUMMARY_JOB_TABLE_NAME = "CommentContextSummaryJob"


def ensure_comment_context_summary_job_table_exists(enterprise_name: Optional[str] = None) -> None:
    """Create CommentContextSummaryJob table if it doesn't exist. Safe – won't affect existing data."""
    try:
        with get_enterprise_audience_connection(enterprise_name) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS "{COMMENT_CONTEXT_SUMMARY_JOB_TABLE_NAME}" (
                        id TEXT PRIMARY KEY,
                        "audienceRoomId" TEXT NOT NULL,
                        "enterpriseName" TEXT,
                        status TEXT NOT NULL DEFAULT 'PENDING',
                        "totalProfiles" INTEGER NOT NULL DEFAULT 0,
                        "processedProfiles" INTEGER NOT NULL DEFAULT 0,
                        "totalComments" INTEGER NOT NULL DEFAULT 0,
                        "processedComments" INTEGER NOT NULL DEFAULT 0,
                        "summarizedComments" INTEGER NOT NULL DEFAULT 0,
                        "skippedComments" INTEGER NOT NULL DEFAULT 0,
                        "errorCount" INTEGER NOT NULL DEFAULT 0,
                        "currentChunk" INTEGER NOT NULL DEFAULT 0,
                        "totalChunks" INTEGER NOT NULL DEFAULT 0,
                        error TEXT,
                        "createdAt" TIMESTAMP NOT NULL DEFAULT NOW(),
                        "updatedAt" TIMESTAMP NOT NULL DEFAULT NOW()
                    )
                    """
                )
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    CREATE INDEX IF NOT EXISTS idx_comment_context_summary_job_status
                    ON "{COMMENT_CONTEXT_SUMMARY_JOB_TABLE_NAME}" (status)
                    """
                )
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    CREATE INDEX IF NOT EXISTS idx_comment_context_summary_job_audience_room_id
                    ON "{COMMENT_CONTEXT_SUMMARY_JOB_TABLE_NAME}" ("audienceRoomId")
                    """
                )
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    CREATE INDEX IF NOT EXISTS idx_comment_context_summary_job_created_at
                    ON "{COMMENT_CONTEXT_SUMMARY_JOB_TABLE_NAME}" ("createdAt")
                    """
                )
    except Exception as e:
        logger.error(f"Failed to ensure CommentContextSummaryJob table exists: {e}")
        raise


def create_comment_context_summary_job(
    job_id: str,
    audience_room_id: str,
    enterprise_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new CommentContextSummaryJob record."""
    ensure_comment_context_summary_job_table_exists(enterprise_name)

    now = datetime.utcnow()
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO "{COMMENT_CONTEXT_SUMMARY_JOB_TABLE_NAME}"
                (id, "audienceRoomId", "enterpriseName", status,
                 "totalProfiles", "processedProfiles",
                 "totalComments", "processedComments",
                 "summarizedComments", "skippedComments", "errorCount",
                 "currentChunk", "totalChunks",
                 "createdAt", "updatedAt")
                VALUES (%s, %s, %s, %s,
                        %s, %s,
                        %s, %s,
                        %s, %s, %s,
                        %s, %s,
                        %s, %s)
                RETURNING id, "audienceRoomId", "enterpriseName", status,
                          "totalProfiles", "processedProfiles",
                          "totalComments", "processedComments",
                          "summarizedComments", "skippedComments", "errorCount",
                          "currentChunk", "totalChunks",
                          "createdAt", "updatedAt"
                """,
                (
                    job_id,
                    audience_room_id,
                    enterprise_name,
                    "PENDING",
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    now,
                    now,
                ),
            )
            row = cur.fetchone()

    return {
        "job_id": row[0],
        "audience_room_id": row[1],
        "enterprise_name": row[2],
        "status": row[3],
        "total_profiles": row[4],
        "processed_profiles": row[5],
        "total_comments": row[6],
        "processed_comments": row[7],
        "summarized_comments": row[8],
        "skipped_comments": row[9],
        "error_count": row[10],
        "current_chunk": row[11],
        "total_chunks": row[12],
        "created_at": row[13].isoformat() if row[13] else None,
        "updated_at": row[14].isoformat() if row[14] else None,
    }


def get_comment_context_summary_job(
    job_id: str,
    enterprise_name: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Fetch a CommentContextSummaryJob by id."""
    ensure_comment_context_summary_job_table_exists(enterprise_name)
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, "audienceRoomId", "enterpriseName", status,
                       "totalProfiles", "processedProfiles",
                       "totalComments", "processedComments",
                       "summarizedComments", "skippedComments", "errorCount",
                       "currentChunk", "totalChunks",
                       "createdAt", "updatedAt", error
                FROM "{COMMENT_CONTEXT_SUMMARY_JOB_TABLE_NAME}"
                WHERE id = %s
                """,
                (job_id,),
            )
            row = cur.fetchone()
            if not row:
                return None

    return {
        "job_id": row[0],
        "audience_room_id": row[1],
        "enterprise_name": row[2],
        "status": row[3],
        "total_profiles": row[4],
        "processed_profiles": row[5],
        "total_comments": row[6],
        "processed_comments": row[7],
        "summarized_comments": row[8],
        "skipped_comments": row[9],
        "error_count": row[10],
        "current_chunk": row[11],
        "total_chunks": row[12],
        "created_at": row[13].isoformat() if row[13] else None,
        "updated_at": row[14].isoformat() if row[14] else None,
        "error": row[15],
    }


def update_comment_context_summary_job(
    job_id: str,
    enterprise_name: Optional[str] = None,
    **kwargs: Any,
) -> None:
    """Update fields on a CommentContextSummaryJob."""
    ensure_comment_context_summary_job_table_exists(enterprise_name)

    set_clauses = ['"updatedAt" = NOW()']
    values: List[Any] = []

    field_mapping = {
        "status": "status",
        "total_profiles": '"totalProfiles"',
        "processed_profiles": '"processedProfiles"',
        "total_comments": '"totalComments"',
        "processed_comments": '"processedComments"',
        "summarized_comments": '"summarizedComments"',
        "skipped_comments": '"skippedComments"',
        "error_count": '"errorCount"',
        "current_chunk": '"currentChunk"',
        "total_chunks": '"totalChunks"',
        "error": "error",
    }

    for key, column in field_mapping.items():
        if key in kwargs and kwargs[key] is not None:
            set_clauses.append(f"{column} = %s")
            values.append(kwargs[key])

    values.append(job_id)

    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE "{COMMENT_CONTEXT_SUMMARY_JOB_TABLE_NAME}"
                SET {", ".join(set_clauses)}
                WHERE id = %s
                """,
                values,
            )


def get_pending_comment_context_summary_jobs(
    enterprise_name: Optional[str] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """Return recent pending/processing jobs (for debugging/admin)."""
    ensure_comment_context_summary_job_table_exists(enterprise_name)
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, "audienceRoomId", "enterpriseName", status,
                       "totalProfiles", "processedProfiles",
                       "totalComments", "processedComments",
                       "summarizedComments", "skippedComments", "errorCount",
                       "currentChunk", "totalChunks",
                       "createdAt", "updatedAt", error
                FROM "{COMMENT_CONTEXT_SUMMARY_JOB_TABLE_NAME}"
                WHERE status IN ('PENDING', 'PROCESSING')
                ORDER BY "createdAt" DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall() or []

    jobs: List[Dict[str, Any]] = []
    for row in rows:
        jobs.append(
            {
                "job_id": row[0],
                "audience_room_id": row[1],
                "enterprise_name": row[2],
                "status": row[3],
                "total_profiles": row[4],
                "processed_profiles": row[5],
                "total_comments": row[6],
                "processed_comments": row[7],
                "summarized_comments": row[8],
                "skipped_comments": row[9],
                "error_count": row[10],
                "current_chunk": row[11],
                "total_chunks": row[12],
                "created_at": row[13].isoformat() if row[13] else None,
                "updated_at": row[14].isoformat() if row[14] else None,
                "error": row[15],
            }
        )
    return jobs

