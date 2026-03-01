"""Reads and updates CommentContextSummaryJob records by id."""

from typing import Any, Dict, List, Optional

from app.database.connection import get_enterprise_audience_connection

from app.services.comment_context_summary_service.repositories.comment_context_summary_job_table_setup import (
    COMMENT_CONTEXT_SUMMARY_JOB_TABLE_NAME,
    ensure_comment_context_summary_job_table_exists,
)


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
