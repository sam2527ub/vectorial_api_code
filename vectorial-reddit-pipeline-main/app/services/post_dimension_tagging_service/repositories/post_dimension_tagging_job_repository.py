"""PostDimensionTaggingJob repository for post dimension tagging service."""
import uuid
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any
from psycopg2.extras import RealDictCursor, Json
from app.database.database_connection_pool_manager import get_enterprise_audience_connection
from app.database.models.post_dimension_tagging_job import PostDimensionTaggingJob
from app.services.post_dimension_tagging_service.config import POST_DIMENSION_TAGGING_JOB_TABLE_NAME

logger = logging.getLogger(__name__)


def ensure_post_dimension_tagging_job_table_exists(enterprise_name: Optional[str] = None):
    """Ensure the PostDimensionTaggingJob table exists, create it if it doesn't. Silent if table already exists."""
    try:
        with get_enterprise_audience_connection(enterprise_name) as conn:
            with conn.cursor() as cur:
                cur.execute(f"""
                    CREATE TABLE IF NOT EXISTS "{POST_DIMENSION_TAGGING_JOB_TABLE_NAME}" (
                        id TEXT PRIMARY KEY,
                        "audienceRoomId" TEXT NOT NULL,
                        "enterpriseName" TEXT,
                        status TEXT NOT NULL DEFAULT 'PENDING',
                        "totalPosts" INTEGER NOT NULL DEFAULT 0,
                        "processedPosts" INTEGER NOT NULL DEFAULT 0,
                        "taggedPosts" INTEGER NOT NULL DEFAULT 0,
                        "failedPosts" INTEGER NOT NULL DEFAULT 0,
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
                    CREATE INDEX IF NOT EXISTS idx_post_dimension_tagging_job_status 
                    ON "{POST_DIMENSION_TAGGING_JOB_TABLE_NAME}" (status)
                """)
                cur.execute(f"""
                    CREATE INDEX IF NOT EXISTS idx_post_dimension_tagging_job_audience_room_id 
                    ON "{POST_DIMENSION_TAGGING_JOB_TABLE_NAME}" ("audienceRoomId")
                """)
                cur.execute(f"""
                    CREATE INDEX IF NOT EXISTS idx_post_dimension_tagging_job_created_at 
                    ON "{POST_DIMENSION_TAGGING_JOB_TABLE_NAME}" ("createdAt")
                """)
                conn.commit()
                # Don't log - table check is silent to avoid log spam
    except Exception as e:
        logger.error(f"Failed to ensure PostDimensionTaggingJob table exists: {e}")
        raise


def create_post_dimension_tagging_job(
    audience_room_id: str,
    enterprise_name: Optional[str] = None,
    job_id: Optional[str] = None
) -> PostDimensionTaggingJob:
    """Create a new PostDimensionTaggingJob record."""
    ensure_post_dimension_tagging_job_table_exists(enterprise_name)
    
    if not job_id:
        job_id = str(uuid.uuid4())
    
    now = datetime.utcnow()
    
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"""
                INSERT INTO "{POST_DIMENSION_TAGGING_JOB_TABLE_NAME}" 
                (id, "audienceRoomId", "enterpriseName", status, "totalPosts", "processedPosts", 
                 "taggedPosts", "failedPosts", "createdAt", "updatedAt")
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (job_id, audience_room_id, enterprise_name, 'PENDING', 0, 0, 0, 0, now, now)
            )
            row = cur.fetchone()
            return PostDimensionTaggingJob(row)


def find_post_dimension_tagging_job_by_id(
    job_id: str,
    enterprise_name: Optional[str] = None
) -> Optional[PostDimensionTaggingJob]:
    """Find a PostDimensionTaggingJob by ID."""
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f'SELECT * FROM "{POST_DIMENSION_TAGGING_JOB_TABLE_NAME}" WHERE id = %s',
                (job_id,)
            )
            row = cur.fetchone()
            return PostDimensionTaggingJob(row) if row else None


def get_pending_jobs(
    limit: int = 10,
    enterprise_name: Optional[str] = None
) -> List[PostDimensionTaggingJob]:
    """Get pending jobs for processing (for polling)."""
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if enterprise_name:
                cur.execute(
                    f"""
                    SELECT * FROM "{POST_DIMENSION_TAGGING_JOB_TABLE_NAME}" 
                    WHERE status = 'PENDING' AND ("enterpriseName" = %s OR "enterpriseName" IS NULL)
                    ORDER BY "createdAt" ASC
                    LIMIT %s
                    """,
                    (enterprise_name, limit)
                )
            else:
                cur.execute(
                    f"""
                    SELECT * FROM "{POST_DIMENSION_TAGGING_JOB_TABLE_NAME}" 
                    WHERE status = 'PENDING'
                    ORDER BY "createdAt" ASC
                    LIMIT %s
                    """,
                    (limit,)
                )
            rows = cur.fetchall()
            return [PostDimensionTaggingJob(row) for row in rows]


def update_job_status(
    job_id: str,
    status: Optional[str] = None,
    total_posts: Optional[int] = None,
    processed_posts: Optional[int] = None,
    tagged_posts: Optional[int] = None,
    failed_posts: Optional[int] = None,
    result: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
    started: bool = False,
    completed: bool = False,
    enterprise_name: Optional[str] = None
) -> Optional[PostDimensionTaggingJob]:
    """Update a PostDimensionTaggingJob record."""
    set_clauses = []
    values = []
    
    if status is not None:
        set_clauses.append('status = %s')
        values.append(status)
    
    if total_posts is not None:
        set_clauses.append('"totalPosts" = %s')
        values.append(total_posts)
    
    if processed_posts is not None:
        set_clauses.append('"processedPosts" = %s')
        values.append(processed_posts)
    
    if tagged_posts is not None:
        set_clauses.append('"taggedPosts" = %s')
        values.append(tagged_posts)
    
    if failed_posts is not None:
        set_clauses.append('"failedPosts" = %s')
        values.append(failed_posts)
    
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
            query = f'UPDATE "{POST_DIMENSION_TAGGING_JOB_TABLE_NAME}" SET {", ".join(set_clauses)} WHERE id = %s RETURNING *'
            cur.execute(query, values)
            row = cur.fetchone()
            return PostDimensionTaggingJob(row) if row else None


def mark_job_completed(
    job_id: str,
    result: Dict[str, Any],
    tagged_posts: int,
    failed_posts: int,
    total_posts: Optional[int] = None,
    processed_posts: Optional[int] = None,
    enterprise_name: Optional[str] = None
) -> Optional[PostDimensionTaggingJob]:
    """Mark a job as completed with results."""
    kwargs = dict(
        job_id=job_id,
        status='COMPLETED',
        tagged_posts=tagged_posts,
        failed_posts=failed_posts,
        result=result,
        completed=True,
        enterprise_name=enterprise_name,
    )
    if total_posts is not None:
        kwargs['total_posts'] = total_posts
    if processed_posts is not None:
        kwargs['processed_posts'] = processed_posts
    return update_job_status(**kwargs)


def mark_job_failed(
    job_id: str,
    error: str,
    enterprise_name: Optional[str] = None
) -> Optional[PostDimensionTaggingJob]:
    """Mark a job as failed with error message."""
    return update_job_status(
        job_id=job_id,
        status='FAILED',
        error=error,
        completed=True,
        enterprise_name=enterprise_name
    )
