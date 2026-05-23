"""CommentContextSummaryJob repository for comment context summary service."""
import uuid
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any
from psycopg2.extras import RealDictCursor, Json
from app.database.database_connection_pool_manager import get_enterprise_audience_connection
from app.database.models.comment_context_summary_job import CommentContextSummaryJob

logger = logging.getLogger(__name__)

COMMENT_CONTEXT_SUMMARY_JOB_TABLE_NAME = "CommentContextSummaryJob"


def ensure_comment_context_summary_job_table_exists(enterprise_name: Optional[str] = None):
    """Ensure the CommentContextSummaryJob table exists, create it if it doesn't. Silent if table already exists."""
    try:
        with get_enterprise_audience_connection(enterprise_name) as conn:
            with conn.cursor() as cur:
                cur.execute(f"""
                    CREATE TABLE IF NOT EXISTS "{COMMENT_CONTEXT_SUMMARY_JOB_TABLE_NAME}" (
                        id TEXT PRIMARY KEY,
                        "audienceRoomId" TEXT NOT NULL,
                        "enterpriseName" TEXT,
                        status TEXT NOT NULL DEFAULT 'PENDING',
                        "totalProfiles" INTEGER NOT NULL DEFAULT 0,
                        "processedProfiles" INTEGER NOT NULL DEFAULT 0,
                        "totalComments" INTEGER NOT NULL DEFAULT 0,
                        "enrichedComments" INTEGER NOT NULL DEFAULT 0,
                        "skippedComments" INTEGER NOT NULL DEFAULT 0,
                        "failedComments" INTEGER NOT NULL DEFAULT 0,
                        error TEXT,
                        result JSONB,
                        "createdAt" TIMESTAMP NOT NULL DEFAULT NOW(),
                        "updatedAt" TIMESTAMP NOT NULL DEFAULT NOW(),
                        "startedAt" TIMESTAMP,
                        "completedAt" TIMESTAMP
                    )
                """)
                
                # Create indexes for common queries (if not exists)
                cur.execute(f"""
                    CREATE INDEX IF NOT EXISTS idx_comment_context_summary_job_status 
                    ON "{COMMENT_CONTEXT_SUMMARY_JOB_TABLE_NAME}" (status)
                """)
                cur.execute(f"""
                    CREATE INDEX IF NOT EXISTS idx_comment_context_summary_job_audience_room_id 
                    ON "{COMMENT_CONTEXT_SUMMARY_JOB_TABLE_NAME}" ("audienceRoomId")
                """)
                cur.execute(f"""
                    CREATE INDEX IF NOT EXISTS idx_comment_context_summary_job_created_at 
                    ON "{COMMENT_CONTEXT_SUMMARY_JOB_TABLE_NAME}" ("createdAt")
                """)
                conn.commit()
                # Don't log - table check is silent to avoid log spam
    except Exception as e:
        logger.error(f"Failed to ensure CommentContextSummaryJob table exists: {e}")
        raise


def create_comment_context_summary_job(
    audience_room_id: str,
    enterprise_name: Optional[str] = None,
    job_id: Optional[str] = None
) -> CommentContextSummaryJob:
    """Create a new CommentContextSummaryJob record."""
    ensure_comment_context_summary_job_table_exists(enterprise_name)
    
    if not job_id:
        job_id = str(uuid.uuid4())
    
    now = datetime.utcnow()
    
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"""
                INSERT INTO "{COMMENT_CONTEXT_SUMMARY_JOB_TABLE_NAME}" 
                (id, "audienceRoomId", "enterpriseName", status, "totalProfiles", "processedProfiles", 
                 "totalComments", "enrichedComments", "skippedComments", "failedComments", "createdAt", "updatedAt")
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (job_id, audience_room_id, enterprise_name, 'PENDING', 0, 0, 0, 0, 0, 0, now, now)
            )
            row = cur.fetchone()
            return CommentContextSummaryJob(row)

def find_comment_context_summary_job_by_id(
    job_id: str,
    enterprise_name: Optional[str] = None
) -> Optional[CommentContextSummaryJob]:
    """Find a CommentContextSummaryJob by ID."""
    ensure_comment_context_summary_job_table_exists(enterprise_name)

    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f'SELECT * FROM "{COMMENT_CONTEXT_SUMMARY_JOB_TABLE_NAME}" WHERE id = %s',
                (job_id,)
            )
            row = cur.fetchone()
            return CommentContextSummaryJob(row) if row else None


def update_job_status(
    job_id: str,
    status: Optional[str] = None,
    total_profiles: Optional[int] = None,
    processed_profiles: Optional[int] = None,
    total_comments: Optional[int] = None,
    enriched_comments: Optional[int] = None,
    skipped_comments: Optional[int] = None,
    failed_comments: Optional[int] = None,
    result: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
    started: bool = False,
    completed: bool = False,
    enterprise_name: Optional[str] = None
) -> Optional[CommentContextSummaryJob]:
    """Update a CommentContextSummaryJob record."""
    set_clauses = []
    values = []
    
    if status is not None:
        set_clauses.append('status = %s')
        values.append(status)
    
    if total_profiles is not None:
        set_clauses.append('"totalProfiles" = %s')
        values.append(total_profiles)
    
    if processed_profiles is not None:
        set_clauses.append('"processedProfiles" = %s')
        values.append(processed_profiles)
    
    if total_comments is not None:
        set_clauses.append('"totalComments" = %s')
        values.append(total_comments)
    
    if enriched_comments is not None:
        set_clauses.append('"enrichedComments" = %s')
        values.append(enriched_comments)
    
    if skipped_comments is not None:
        set_clauses.append('"skippedComments" = %s')
        values.append(skipped_comments)
    
    if failed_comments is not None:
        set_clauses.append('"failedComments" = %s')
        values.append(failed_comments)
    
    if result is not None:
        set_clauses.append('result = %s')
        values.append(Json(result))
    
    if error is not None:
        set_clauses.append('error = %s')
        values.append(error)
    
    if started:
        set_clauses.append('"startedAt" = %s')
        values.append(datetime.utcnow())
    
    if completed:
        set_clauses.append('"completedAt" = %s')
        values.append(datetime.utcnow())
        if status is None:
            set_clauses.append('status = %s')
            values.append('COMPLETED')
    
    set_clauses.append('"updatedAt" = %s')
    values.append(datetime.utcnow())
    values.append(job_id)
    
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            query = f'UPDATE "{COMMENT_CONTEXT_SUMMARY_JOB_TABLE_NAME}" SET {", ".join(set_clauses)} WHERE id = %s RETURNING *'
            cur.execute(query, values)
            row = cur.fetchone()
            return CommentContextSummaryJob(row) if row else None


def mark_job_completed(
    job_id: str,
    result: Dict[str, Any],
    enriched_comments: int,
    skipped_comments: int,
    failed_comments: int,
    total_profiles: Optional[int] = None,
    processed_profiles: Optional[int] = None,
    total_comments: Optional[int] = None,
    enterprise_name: Optional[str] = None
) -> Optional[CommentContextSummaryJob]:
    """Mark a job as completed with results."""
    kwargs = {
        'job_id': job_id,
        'status': 'COMPLETED',
        'result': result,
        'enriched_comments': enriched_comments,
        'skipped_comments': skipped_comments,
        'failed_comments': failed_comments,
        'completed': True,
        'enterprise_name': enterprise_name,
    }
    if total_profiles is not None:
        kwargs['total_profiles'] = total_profiles
    if processed_profiles is not None:
        kwargs['processed_profiles'] = processed_profiles
    if total_comments is not None:
        kwargs['total_comments'] = total_comments
    return update_job_status(**kwargs)


def mark_job_failed(
    job_id: str,
    error: str,
    enterprise_name: Optional[str] = None
) -> Optional[CommentContextSummaryJob]:
    """Mark a job as failed with error message."""
    return update_job_status(
        job_id=job_id,
        status='FAILED',
        error=error,
        completed=True,
        enterprise_name=enterprise_name
    )
