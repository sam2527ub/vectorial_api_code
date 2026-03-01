"""Ensures CommentContextSummaryJob table and indexes exist; adds missing columns."""

import logging
from typing import Optional

from app.database.connection import get_enterprise_audience_connection

logger = logging.getLogger(__name__)

COMMENT_CONTEXT_SUMMARY_JOB_TABLE_NAME = "CommentContextSummaryJob"


def ensure_comment_context_summary_job_table_exists(
    enterprise_name: Optional[str] = None,
) -> None:
    """Create CommentContextSummaryJob table if it doesn't exist. Add missing columns on existing tables."""
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
            for col_def in [
                ('"processedComments"', 'INTEGER NOT NULL DEFAULT 0'),
                ('"summarizedComments"', 'INTEGER NOT NULL DEFAULT 0'),
                ('"skippedComments"', 'INTEGER NOT NULL DEFAULT 0'),
                ('"errorCount"', 'INTEGER NOT NULL DEFAULT 0'),
                ('"currentChunk"', 'INTEGER NOT NULL DEFAULT 0'),
                ('"totalChunks"', 'INTEGER NOT NULL DEFAULT 0'),
            ]:
                with conn.cursor() as cur:
                    cur.execute(
                        f'ALTER TABLE "{COMMENT_CONTEXT_SUMMARY_JOB_TABLE_NAME}" ADD COLUMN IF NOT EXISTS {col_def[0]} {col_def[1]}'
                    )
            for index_sql in [
                f'CREATE INDEX IF NOT EXISTS idx_comment_context_summary_job_status ON "{COMMENT_CONTEXT_SUMMARY_JOB_TABLE_NAME}" (status)',
                f'CREATE INDEX IF NOT EXISTS idx_comment_context_summary_job_audience_room_id ON "{COMMENT_CONTEXT_SUMMARY_JOB_TABLE_NAME}" ("audienceRoomId")',
                f'CREATE INDEX IF NOT EXISTS idx_comment_context_summary_job_created_at ON "{COMMENT_CONTEXT_SUMMARY_JOB_TABLE_NAME}" ("createdAt")',
            ]:
                with conn.cursor() as cur:
                    cur.execute(index_sql)
    except Exception as e:
        logger.error("Failed to ensure CommentContextSummaryJob table exists: %s", e)
        raise
