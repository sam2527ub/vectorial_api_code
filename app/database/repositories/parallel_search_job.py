"""ParallelSearchJob repository - audience database."""
import uuid
from datetime import datetime
from typing import Optional, Dict, Any

from psycopg2.extras import RealDictCursor, Json

from app.database.pool import get_enterprise_audience_connection
from app.database.models import ParallelSearchJob


def create_parallel_search_job(
    query: str,
    model: str = 'core',
    match_limit: int = 100,
    enterprise_name: Optional[str] = None
) -> ParallelSearchJob:
    job_id = str(uuid.uuid4())
    now = datetime.utcnow()
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS "ParallelSearchJob" (
                    id VARCHAR(255) PRIMARY KEY,
                    status VARCHAR(50) DEFAULT 'PENDING',
                    query TEXT NOT NULL,
                    model VARCHAR(50) DEFAULT 'core',
                    "matchLimit" INTEGER DEFAULT 100,
                    "parallelRunId" VARCHAR(255),
                    profiles JSONB,
                    result JSONB,
                    error TEXT,
                    "enterpriseName" VARCHAR(50),
                    "createdAt" TIMESTAMP DEFAULT NOW(),
                    "updatedAt" TIMESTAMP DEFAULT NOW()
                )
            """)
            cur.execute('CREATE INDEX IF NOT EXISTS idx_parallel_search_job_status ON "ParallelSearchJob" (status)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_parallel_search_job_created ON "ParallelSearchJob" ("createdAt")')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_parallel_search_job_enterprise ON "ParallelSearchJob" ("enterpriseName")')
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO "ParallelSearchJob"
                (id, status, query, model, "matchLimit", "enterpriseName", "createdAt", "updatedAt")
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (job_id, 'PENDING', query, model, match_limit, enterprise_name, now, now)
            )
            row = cur.fetchone()
            return ParallelSearchJob(row)


def find_parallel_search_job_by_id(job_id: str, enterprise_name: Optional[str] = None) -> Optional[ParallelSearchJob]:
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT * FROM "ParallelSearchJob" WHERE id = %s', (job_id,))
            row = cur.fetchone()
            return ParallelSearchJob(row) if row else None


def update_parallel_search_job(
    job_id: str,
    data: Dict[str, Any],
    enterprise_name: Optional[str] = None
) -> Optional[ParallelSearchJob]:
    if not data:
        return find_parallel_search_job_by_id(job_id, enterprise_name=enterprise_name)
    set_clauses = []
    values = []
    field_mapping = {
        'status': '"status"',
        'parallelRunId': '"parallelRunId"',
        'profiles': '"profiles"',
        'result': '"result"',
        'error': '"error"',
    }
    for key, value in data.items():
        if key in field_mapping:
            set_clauses.append(f'{field_mapping[key]} = %s')
            values.append(Json(value) if key in ('profiles', 'result') and value is not None else value)
    set_clauses.append('"updatedAt" = %s')
    values.append(datetime.utcnow())
    values.append(job_id)
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f'UPDATE "ParallelSearchJob" SET {", ".join(set_clauses)} WHERE id = %s RETURNING *',
                values
            )
            row = cur.fetchone()
            return ParallelSearchJob(row) if row else None
