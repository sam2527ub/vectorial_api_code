"""SummaryJob repository for user profile summarization service."""
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any
from psycopg2.extras import RealDictCursor, Json
from app.services.user_profile_summarization_service.clients.database.factory import get_database_client
from app.database.models.summary_job import SummaryJob

logger = logging.getLogger(__name__)


def ensure_summary_job_table_exists(enterprise_name: Optional[str] = None):
    """Ensure the SummaryJob table exists, create it if it doesn't. Silent if table already exists."""
    db_client = get_database_client()
    try:
        with db_client.get_connection(enterprise_name) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS "SummaryJob" (
                        id TEXT PRIMARY KEY,
                        "audienceRoomId" TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'running',
                        "totalProfiles" INTEGER NOT NULL DEFAULT 0,
                        "processedProfiles" INTEGER NOT NULL DEFAULT 0,
                        "successCount" INTEGER NOT NULL DEFAULT 0,
                        "skippedCount" INTEGER NOT NULL DEFAULT 0,
                        "errorCount" INTEGER NOT NULL DEFAULT 0,
                        profiles JSONB,
                        error TEXT,
                        "startedAt" TIMESTAMP NOT NULL DEFAULT NOW(),
                        "updatedAt" TIMESTAMP NOT NULL DEFAULT NOW(),
                        "completedAt" TIMESTAMP
                    )
                """)
                conn.commit()
                # Don't log - table check is silent to avoid log spam
    except Exception as e:
        logger.error(f"Failed to ensure SummaryJob table exists: {e}")
        raise


def create_summary_job(
    job_id: str,
    audience_room_id: str,
    total_profiles: int,
    profile_ids: Optional[List[str]] = None,
    enterprise_name: Optional[str] = None
) -> SummaryJob:
    """Create a new SummaryJob record."""
    ensure_summary_job_table_exists(enterprise_name)
    
    now = datetime.utcnow()
    # Store profile IDs in profiles JSONB field for tracking
    initial_profiles_data = {
        "profile_ids": profile_ids or [],
        "results": []
    } if profile_ids else None
    
    db_client = get_database_client()
    with db_client.get_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO "SummaryJob" 
                (id, "audienceRoomId", status, "totalProfiles", "processedProfiles", "successCount", "skippedCount", "errorCount", profiles, "startedAt", "updatedAt")
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (job_id, audience_room_id, 'running', total_profiles, 0, 0, 0, 0, Json(initial_profiles_data) if initial_profiles_data else None, now, now)
            )
            row = cur.fetchone()
            return SummaryJob(row)


def find_summary_job_by_id(job_id: str, enterprise_name: Optional[str] = None) -> Optional[SummaryJob]:
    """Find a SummaryJob by ID."""
    # Table should already exist - no need to check on every read
    db_client = get_database_client()
    with db_client.get_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                'SELECT * FROM "SummaryJob" WHERE id = %s',
                (job_id,)
            )
            row = cur.fetchone()
            return SummaryJob(row) if row else None


def update_summary_job(
    job_id: str,
    status: Optional[str] = None,
    processed_profiles: Optional[int] = None,
    success_count: Optional[int] = None,
    skipped_count: Optional[int] = None,
    error_count: Optional[int] = None,
    profiles: Optional[List[Dict[str, Any]]] = None,
    error: Optional[str] = None,
    completed: bool = False,
    enterprise_name: Optional[str] = None
) -> Optional[SummaryJob]:
    """Update a SummaryJob record."""
    # Table should already exist - no need to check on every update
    set_clauses = []
    values = []
    
    if status is not None:
        set_clauses.append('status = %s')
        values.append(status)
    
    if processed_profiles is not None:
        set_clauses.append('"processedProfiles" = %s')
        values.append(processed_profiles)
    
    if success_count is not None:
        set_clauses.append('"successCount" = %s')
        values.append(success_count)
    
    if skipped_count is not None:
        set_clauses.append('"skippedCount" = %s')
        values.append(skipped_count)
    
    if error_count is not None:
        set_clauses.append('"errorCount" = %s')
        values.append(error_count)
    
    if profiles is not None:
        set_clauses.append('profiles = %s')
        values.append(Json(profiles))
    
    if error is not None:
        set_clauses.append('error = %s')
        values.append(error)
    
    if completed:
        set_clauses.append('"completedAt" = %s')
        values.append(datetime.utcnow())
    
    set_clauses.append('"updatedAt" = %s')
    values.append(datetime.utcnow())
    values.append(job_id)
    
    db_client = get_database_client()
    with db_client.get_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            query = f'UPDATE "SummaryJob" SET {", ".join(set_clauses)} WHERE id = %s RETURNING *'
            cur.execute(query, values)
            row = cur.fetchone()
            return SummaryJob(row) if row else None
