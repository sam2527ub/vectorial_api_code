"""Creates new CommentContextSummaryJob records in the database."""

from datetime import datetime
from typing import Any, Dict, Optional

from app.database.connection import get_enterprise_audience_connection

from app.services.comment_context_summary_service.repositories.comment_context_summary_job_table_setup import (
    COMMENT_CONTEXT_SUMMARY_JOB_TABLE_NAME,
    ensure_comment_context_summary_job_table_exists,
)


def create_comment_context_summary_job(
    job_id: str,
    audience_room_id: str,
    enterprise_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new CommentContextSummaryJob record and return its data."""
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
