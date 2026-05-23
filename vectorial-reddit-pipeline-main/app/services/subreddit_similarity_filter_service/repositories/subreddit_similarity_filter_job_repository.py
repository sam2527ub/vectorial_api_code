"""SubredditSimilarityFilterJob repository."""
import uuid
import logging
from datetime import datetime
from typing import Optional, Dict, Any
from psycopg2.extras import RealDictCursor, Json
from app.database.database_connection_pool_manager import get_enterprise_audience_connection
from app.database.models.subreddit_similarity_filter_job import SubredditSimilarityFilterJob
from app.services.subreddit_similarity_filter_service.config import SUBREDDIT_SIMILARITY_FILTER_JOB_TABLE_NAME

logger = logging.getLogger(__name__)


def ensure_table_exists(enterprise_name: Optional[str] = None):
    """Ensure the job table exists, create if not."""
    try:
        with get_enterprise_audience_connection(enterprise_name) as conn:
            with conn.cursor() as cur:
                cur.execute(f"""
                    CREATE TABLE IF NOT EXISTS "{SUBREDDIT_SIMILARITY_FILTER_JOB_TABLE_NAME}" (
                        id TEXT PRIMARY KEY,
                        status TEXT NOT NULL DEFAULT 'PENDING',
                        "enterpriseName" TEXT,
                        "totalSubreddits" INTEGER NOT NULL DEFAULT 0,
                        "checkedSubreddits" INTEGER NOT NULL DEFAULT 0,
                        "totalUsersBefore" INTEGER NOT NULL DEFAULT 0,
                        "totalUsersAfter" INTEGER NOT NULL DEFAULT 0,
                        error TEXT,
                        result JSONB,
                        "inputS3Key" TEXT,
                        "resultS3Key" TEXT,
                        "createdAt" TIMESTAMP NOT NULL DEFAULT NOW(),
                        "updatedAt" TIMESTAMP NOT NULL DEFAULT NOW(),
                        "startedAt" TIMESTAMP,
                        "completedAt" TIMESTAMP
                    )
                """)
                cur.execute(f"""
                    CREATE INDEX IF NOT EXISTS idx_sub_sim_filter_job_status
                    ON "{SUBREDDIT_SIMILARITY_FILTER_JOB_TABLE_NAME}" (status)
                """)
                cur.execute(f"""
                    CREATE INDEX IF NOT EXISTS idx_sub_sim_filter_job_created_at
                    ON "{SUBREDDIT_SIMILARITY_FILTER_JOB_TABLE_NAME}" ("createdAt")
                """)
                conn.commit()
    except Exception as e:
        logger.error(f"Failed to ensure SubredditSimilarityFilterJob table exists: {e}")
        raise


def create_subreddit_similarity_filter_job(
    enterprise_name: Optional[str] = None,
    input_s3_key: Optional[str] = None,
    job_id: Optional[str] = None,
) -> SubredditSimilarityFilterJob:
    """Create a new job record."""
    ensure_table_exists(enterprise_name)
    if not job_id:
        job_id = str(uuid.uuid4())
    now = datetime.utcnow()
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"""
                INSERT INTO "{SUBREDDIT_SIMILARITY_FILTER_JOB_TABLE_NAME}"
                (id, status, "enterpriseName", "totalSubreddits", "checkedSubreddits",
                 "totalUsersBefore", "totalUsersAfter", "inputS3Key", "createdAt", "updatedAt")
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (job_id, 'PENDING', enterprise_name, 0, 0, 0, 0, input_s3_key, now, now)
            )
            row = cur.fetchone()
            return SubredditSimilarityFilterJob(row)


def find_subreddit_similarity_filter_job_by_id(
    job_id: str,
    enterprise_name: Optional[str] = None,
) -> Optional[SubredditSimilarityFilterJob]:
    """Find a job by ID."""
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f'SELECT * FROM "{SUBREDDIT_SIMILARITY_FILTER_JOB_TABLE_NAME}" WHERE id = %s',
                (job_id,)
            )
            row = cur.fetchone()
            return SubredditSimilarityFilterJob(row) if row else None


def update_job_status(
    job_id: str,
    status: Optional[str] = None,
    total_subreddits: Optional[int] = None,
    checked_subreddits: Optional[int] = None,
    total_users_before: Optional[int] = None,
    total_users_after: Optional[int] = None,
    result: Optional[Dict[str, Any]] = None,
    result_s3_key: Optional[str] = None,
    error: Optional[str] = None,
    started: bool = False,
    completed: bool = False,
    enterprise_name: Optional[str] = None,
) -> Optional[SubredditSimilarityFilterJob]:
    """Update a job record."""
    set_clauses = []
    values = []

    if status is not None:
        set_clauses.append('status = %s')
        values.append(status)
    if total_subreddits is not None:
        set_clauses.append('"totalSubreddits" = %s')
        values.append(total_subreddits)
    if checked_subreddits is not None:
        set_clauses.append('"checkedSubreddits" = %s')
        values.append(checked_subreddits)
    if total_users_before is not None:
        set_clauses.append('"totalUsersBefore" = %s')
        values.append(total_users_before)
    if total_users_after is not None:
        set_clauses.append('"totalUsersAfter" = %s')
        values.append(total_users_after)
    if result is not None:
        set_clauses.append('result = %s')
        values.append(Json(result))
    if result_s3_key is not None:
        set_clauses.append('"resultS3Key" = %s')
        values.append(result_s3_key)
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
            query = f'UPDATE "{SUBREDDIT_SIMILARITY_FILTER_JOB_TABLE_NAME}" SET {", ".join(set_clauses)} WHERE id = %s RETURNING *'
            cur.execute(query, values)
            row = cur.fetchone()
            return SubredditSimilarityFilterJob(row) if row else None


def mark_job_completed(
    job_id: str,
    result: Dict[str, Any],
    total_users_before: int,
    total_users_after: int,
    total_subreddits: Optional[int] = None,
    checked_subreddits: Optional[int] = None,
    result_s3_key: Optional[str] = None,
    enterprise_name: Optional[str] = None,
) -> Optional[SubredditSimilarityFilterJob]:
    """Mark a job as completed with results."""
    kwargs: Dict[str, Any] = dict(
        job_id=job_id,
        status='COMPLETED',
        total_users_before=total_users_before,
        total_users_after=total_users_after,
        result=result,
        completed=True,
        enterprise_name=enterprise_name,
    )
    if total_subreddits is not None:
        kwargs['total_subreddits'] = total_subreddits
    if checked_subreddits is not None:
        kwargs['checked_subreddits'] = checked_subreddits
    if result_s3_key is not None:
        kwargs['result_s3_key'] = result_s3_key
    return update_job_status(**kwargs)


def mark_job_failed(
    job_id: str,
    error: str,
    enterprise_name: Optional[str] = None,
) -> Optional[SubredditSimilarityFilterJob]:
    """Mark a job as failed with error message."""
    return update_job_status(
        job_id=job_id,
        status='FAILED',
        error=error,
        completed=True,
        enterprise_name=enterprise_name,
    )
