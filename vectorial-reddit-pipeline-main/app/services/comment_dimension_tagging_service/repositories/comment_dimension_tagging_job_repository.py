"""CommentDimensionTaggingJob repository for comment dimension tagging service."""
import uuid
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any
from psycopg2.extras import RealDictCursor, Json
from app.database.database_connection_pool_manager import get_enterprise_audience_connection
from app.database.models.comment_dimension_tagging_job import CommentDimensionTaggingJob
from app.services.comment_dimension_tagging_service.config import COMMENT_DIMENSION_TAGGING_JOB_TABLE_NAME

logger = logging.getLogger(__name__)


def ensure_comment_dimension_tagging_job_table_exists(enterprise_name: Optional[str] = None):
    """Ensure the CommentDimensionTaggingJob table exists, create it if it doesn't. Silent if table already exists."""
    try:
        with get_enterprise_audience_connection(enterprise_name) as conn:
            with conn.cursor() as cur:
                cur.execute(f"""
                    CREATE TABLE IF NOT EXISTS "{COMMENT_DIMENSION_TAGGING_JOB_TABLE_NAME}" (
                        id TEXT PRIMARY KEY,
                        "audienceRoomId" TEXT NOT NULL,
                        "enterpriseName" TEXT,
                        status TEXT NOT NULL DEFAULT 'PENDING',
                        "totalComments" INTEGER NOT NULL DEFAULT 0,
                        "processedComments" INTEGER NOT NULL DEFAULT 0,
                        "taggedComments" INTEGER NOT NULL DEFAULT 0,
                        "failedComments" INTEGER NOT NULL DEFAULT 0,
                        "skippedComments" INTEGER NOT NULL DEFAULT 0,
                        "totalChunks" INTEGER NOT NULL DEFAULT 0,
                        "completedChunks" INTEGER NOT NULL DEFAULT 0,
                        "chunkSizeProfiles" INTEGER,
                        metadata JSONB,
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
                    CREATE INDEX IF NOT EXISTS idx_comment_dimension_tagging_job_status 
                    ON "{COMMENT_DIMENSION_TAGGING_JOB_TABLE_NAME}" (status)
                """)
                cur.execute(f"""
                    CREATE INDEX IF NOT EXISTS idx_comment_dimension_tagging_job_audience_room_id 
                    ON "{COMMENT_DIMENSION_TAGGING_JOB_TABLE_NAME}" ("audienceRoomId")
                """)
                cur.execute(f"""
                    CREATE INDEX IF NOT EXISTS idx_comment_dimension_tagging_job_created_at 
                    ON "{COMMENT_DIMENSION_TAGGING_JOB_TABLE_NAME}" ("createdAt")
                """)
                conn.commit()
                # Don't log - table check is silent to avoid log spam
    except Exception as e:
        logger.error(f"Failed to ensure CommentDimensionTaggingJob table exists: {e}")
        raise


def create_comment_dimension_tagging_job(
    audience_room_id: str,
    enterprise_name: Optional[str] = None,
    job_id: Optional[str] = None
) -> CommentDimensionTaggingJob:
    """Create a new CommentDimensionTaggingJob record."""
    ensure_comment_dimension_tagging_job_table_exists(enterprise_name)
    
    if not job_id:
        job_id = str(uuid.uuid4())
    
    now = datetime.utcnow()
    
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"""
                INSERT INTO "{COMMENT_DIMENSION_TAGGING_JOB_TABLE_NAME}" 
                (id, "audienceRoomId", "enterpriseName", status, "totalComments", "processedComments", 
                 "taggedComments", "failedComments", "skippedComments", "totalChunks", "completedChunks",
                 "createdAt", "updatedAt")
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (job_id, audience_room_id, enterprise_name, 'PENDING', 0, 0, 0, 0, 0, 0, 0, now, now)
            )
            row = cur.fetchone()
            return CommentDimensionTaggingJob(row)


def find_comment_dimension_tagging_job_by_id(
    job_id: str,
    enterprise_name: Optional[str] = None
) -> Optional[CommentDimensionTaggingJob]:
    """Find a CommentDimensionTaggingJob by ID."""
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f'SELECT * FROM "{COMMENT_DIMENSION_TAGGING_JOB_TABLE_NAME}" WHERE id = %s',
                (job_id,)
            )
            row = cur.fetchone()
            return CommentDimensionTaggingJob(row) if row else None


def get_pending_jobs(
    limit: int = 10,
    enterprise_name: Optional[str] = None
) -> List[CommentDimensionTaggingJob]:
    """Get pending jobs for processing (for polling)."""
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if enterprise_name:
                cur.execute(
                    f"""
                    SELECT * FROM "{COMMENT_DIMENSION_TAGGING_JOB_TABLE_NAME}" 
                    WHERE status = 'PENDING' AND ("enterpriseName" = %s OR "enterpriseName" IS NULL)
                    ORDER BY "createdAt" ASC
                    LIMIT %s
                    """,
                    (enterprise_name, limit)
                )
            else:
                cur.execute(
                    f"""
                    SELECT * FROM "{COMMENT_DIMENSION_TAGGING_JOB_TABLE_NAME}" 
                    WHERE status = 'PENDING'
                    ORDER BY "createdAt" ASC
                    LIMIT %s
                    """,
                    (limit,)
                )
            rows = cur.fetchall()
            return [CommentDimensionTaggingJob(row) for row in rows]


def update_job_status(
    job_id: str,
    status: Optional[str] = None,
    total_comments: Optional[int] = None,
    processed_comments: Optional[int] = None,
    tagged_comments: Optional[int] = None,
    failed_comments: Optional[int] = None,
    skipped_comments: Optional[int] = None,
    total_chunks: Optional[int] = None,
    chunk_size_profiles: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
    result: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
    started: bool = False,
    completed: bool = False,
    enterprise_name: Optional[str] = None
) -> Optional[CommentDimensionTaggingJob]:
    """Update a CommentDimensionTaggingJob record."""
    set_clauses = []
    values = []
    
    if status is not None:
        set_clauses.append('status = %s')
        values.append(status)
    
    if total_comments is not None:
        set_clauses.append('"totalComments" = %s')
        values.append(total_comments)
    
    if processed_comments is not None:
        set_clauses.append('"processedComments" = %s')
        values.append(processed_comments)
    
    if tagged_comments is not None:
        set_clauses.append('"taggedComments" = %s')
        values.append(tagged_comments)
    
    if failed_comments is not None:
        set_clauses.append('"failedComments" = %s')
        values.append(failed_comments)
    
    if skipped_comments is not None:
        set_clauses.append('"skippedComments" = %s')
        values.append(skipped_comments)
    
    if total_chunks is not None:
        set_clauses.append('"totalChunks" = %s')
        values.append(total_chunks)
    
    if chunk_size_profiles is not None:
        set_clauses.append('"chunkSizeProfiles" = %s')
        values.append(chunk_size_profiles)
    
    if metadata is not None:
        set_clauses.append('metadata = %s')
        values.append(Json(metadata))
    
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
            query = f'UPDATE "{COMMENT_DIMENSION_TAGGING_JOB_TABLE_NAME}" SET {", ".join(set_clauses)} WHERE id = %s RETURNING *'
            cur.execute(query, values)
            row = cur.fetchone()
            return CommentDimensionTaggingJob(row) if row else None


def increment_completed_chunks(
    job_id: str,
    enterprise_name: Optional[str] = None
) -> int:
    """Atomically increment completed_chunks counter and return new value."""
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"""
                UPDATE "{COMMENT_DIMENSION_TAGGING_JOB_TABLE_NAME}"
                SET "completedChunks" = "completedChunks" + 1,
                    "updatedAt" = NOW()
                WHERE id = %s
                RETURNING "completedChunks"
                """,
                (job_id,)
            )
            row = cur.fetchone()
            conn.commit()
            return row['completedChunks'] if row else 0


def mark_job_completed(
    job_id: str,
    result: Dict[str, Any],
    tagged_comments: int,
    failed_comments: int,
    skipped_comments: int,
    total_comments: Optional[int] = None,
    processed_comments: Optional[int] = None,
    enterprise_name: Optional[str] = None
) -> Optional[CommentDimensionTaggingJob]:
    """Mark a job as completed with results."""
    kwargs = dict(
        job_id=job_id,
        status='COMPLETED',
        tagged_comments=tagged_comments,
        failed_comments=failed_comments,
        skipped_comments=skipped_comments,
        result=result,
        completed=True,
        enterprise_name=enterprise_name,
    )
    if total_comments is not None:
        kwargs['total_comments'] = total_comments
    if processed_comments is not None:
        kwargs['processed_comments'] = processed_comments
    return update_job_status(**kwargs)


def mark_job_failed(
    job_id: str,
    error: str,
    enterprise_name: Optional[str] = None
) -> Optional[CommentDimensionTaggingJob]:
    """Mark a job as failed with error message."""
    return update_job_status(
        job_id=job_id,
        status='FAILED',
        error=error,
        completed=True,
        enterprise_name=enterprise_name
    )
