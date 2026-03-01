"""Queries for pending/processing CommentContextSummaryJob records (e.g. for admin/debug)."""

from typing import Dict, List, Any, Optional

from app.database.connection import get_enterprise_audience_connection

from app.services.comment_context_summary_service.repositories.comment_context_summary_job_table_setup import (
    COMMENT_CONTEXT_SUMMARY_JOB_TABLE_NAME,
    ensure_comment_context_summary_job_table_exists,
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
