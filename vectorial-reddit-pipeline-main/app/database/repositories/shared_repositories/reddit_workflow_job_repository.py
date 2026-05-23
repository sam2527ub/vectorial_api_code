"""RedditWorkflowJob repository - shared operations."""
import uuid
import logging
from datetime import datetime
from typing import Optional, Dict, Any
from psycopg2.extras import RealDictCursor, Json
from app.database.database_connection_pool_manager import get_enterprise_audience_connection
from app.database.models.reddit_workflow_job import RedditWorkflowJob

logger = logging.getLogger(__name__)


def ensure_reddit_workflow_job_table_exists(enterprise_name: Optional[str] = None):
    """Ensure the RedditWorkflowJob table exists, create it if it doesn't. Silent if table already exists."""
    try:
        with get_enterprise_audience_connection(enterprise_name) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS "reddit_workflow_jobs" (
                        id TEXT PRIMARY KEY,
                        job_id TEXT UNIQUE NOT NULL,
                        run_id TEXT UNIQUE,
                        user_id TEXT NOT NULL,
                        enterprise_name TEXT,
                        status TEXT NOT NULL DEFAULT 'PENDING',
                        current_step TEXT NOT NULL DEFAULT 'initializing',
                        progress_percentage INTEGER NOT NULL DEFAULT 0,
                        input_data JSONB,
                        step_details JSONB DEFAULT '{}',
                        error_message TEXT,
                        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                        completed_at TIMESTAMP
                    )
                """)
                
                # Create indexes for common queries (if not exists)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_reddit_workflow_jobs_job_id 
                    ON "reddit_workflow_jobs" (job_id)
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_reddit_workflow_jobs_run_id 
                    ON "reddit_workflow_jobs" (run_id)
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_reddit_workflow_jobs_user_id 
                    ON "reddit_workflow_jobs" (user_id)
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_reddit_workflow_jobs_status 
                    ON "reddit_workflow_jobs" (status)
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_reddit_workflow_jobs_enterprise 
                    ON "reddit_workflow_jobs" (enterprise_name)
                """)
                
                conn.commit()
                logger.info(f"Reddit workflow job table ensured for enterprise={enterprise_name}")
    except Exception as e:
        logger.error(f"Failed to ensure reddit_workflow_jobs table exists: {e}")
        raise


def create_reddit_workflow_job(
    job_id: str,
    user_id: str,
    input_data: Dict[str, Any],
    enterprise_name: Optional[str] = None,
    run_id: Optional[str] = None
) -> RedditWorkflowJob:
    """Create a new RedditWorkflowJob record."""
    ensure_reddit_workflow_job_table_exists(enterprise_name)
    
    record_id = str(uuid.uuid4())
    now = datetime.utcnow()
    
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO "reddit_workflow_jobs" 
                (id, job_id, run_id, user_id, enterprise_name, status, current_step, 
                 progress_percentage, input_data, step_details, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (record_id, job_id, run_id, user_id, enterprise_name, 'PENDING', 'initializing',
                 0, Json(input_data), Json({}), now, now)
            )
            row = cur.fetchone()
            logger.info(f"Created reddit workflow job {job_id} for enterprise={enterprise_name}")
            return RedditWorkflowJob(row)


def find_reddit_workflow_job_by_job_id(
    job_id: str,
    enterprise_name: Optional[str] = None
) -> Optional[RedditWorkflowJob]:
    """Find a RedditWorkflowJob by job_id."""
    ensure_reddit_workflow_job_table_exists(enterprise_name)
    
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                'SELECT * FROM "reddit_workflow_jobs" WHERE job_id = %s',
                (job_id,)
            )
            row = cur.fetchone()
            return RedditWorkflowJob(row) if row else None


def find_reddit_workflow_job_by_run_id(
    run_id: str,
    enterprise_name: Optional[str] = None
) -> Optional[RedditWorkflowJob]:
    """Find a RedditWorkflowJob by Vercel run_id."""
    ensure_reddit_workflow_job_table_exists(enterprise_name)
    
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                'SELECT * FROM "reddit_workflow_jobs" WHERE run_id = %s',
                (run_id,)
            )
            row = cur.fetchone()
            return RedditWorkflowJob(row) if row else None


def update_reddit_workflow_job_progress(
    job_id: str,
    status: Optional[str] = None,
    current_step: Optional[str] = None,
    progress_percentage: Optional[int] = None,
    step_details: Optional[Dict[str, Any]] = None,
    error_message: Optional[str] = None,
    completed: bool = False,
    enterprise_name: Optional[str] = None
) -> Optional[RedditWorkflowJob]:
    """Update a RedditWorkflowJob's progress."""
    set_clauses = []
    values = []
    
    if status is not None:
        set_clauses.append('status = %s')
        values.append(status)
    
    if current_step is not None:
        set_clauses.append('current_step = %s')
        values.append(current_step)
    
    if progress_percentage is not None:
        set_clauses.append('progress_percentage = %s')
        values.append(progress_percentage)
    
    if step_details is not None:
        # Merge with existing step_details using jsonb_concat
        set_clauses.append('step_details = step_details || %s')
        values.append(Json(step_details))
    
    if error_message is not None:
        set_clauses.append('error_message = %s')
        values.append(error_message)
    
    if completed:
        set_clauses.append('completed_at = %s')
        values.append(datetime.utcnow())
    
    # Always update updated_at
    set_clauses.append('updated_at = %s')
    values.append(datetime.utcnow())
    
    # Add job_id for WHERE clause
    values.append(job_id)
    
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            query = f'UPDATE "reddit_workflow_jobs" SET {", ".join(set_clauses)} WHERE job_id = %s RETURNING *'
            cur.execute(query, values)
            row = cur.fetchone()
            if row:
                logger.info(f"Updated reddit workflow job {job_id}: status={status}, step={current_step}, progress={progress_percentage}%")
            return RedditWorkflowJob(row) if row else None


def update_reddit_workflow_job_run_id(
    job_id: str,
    run_id: str,
    enterprise_name: Optional[str] = None
) -> Optional[RedditWorkflowJob]:
    """Update a RedditWorkflowJob's Vercel run_id."""
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                '''
                UPDATE "reddit_workflow_jobs" 
                SET run_id = %s, updated_at = %s 
                WHERE job_id = %s 
                RETURNING *
                ''',
                (run_id, datetime.utcnow(), job_id)
            )
            row = cur.fetchone()
            if row:
                logger.info(f"Updated reddit workflow job {job_id} with run_id={run_id}")
            return RedditWorkflowJob(row) if row else None
