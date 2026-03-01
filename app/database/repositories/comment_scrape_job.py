"""CommentScrapeJob repository - audience database."""
import uuid
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

from psycopg2.extras import RealDictCursor, Json

from app.database.pool import get_enterprise_audience_connection
from app.database.models import CommentScrapeJob

logger = logging.getLogger(__name__)


def ensure_comment_scrape_job_table_exists(enterprise_name: Optional[str] = None) -> None:
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS "CommentScrapeJob" (
                    "id" TEXT NOT NULL,
                    "audienceRoomId" TEXT NOT NULL,
                    "enterpriseName" TEXT,
                    "status" TEXT NOT NULL DEFAULT 'PROCESSING',
                    "result" JSONB,
                    "error" TEXT,
                    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    "updatedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT "CommentScrapeJob_pkey" PRIMARY KEY ("id")
                )
            """)
            cur.execute('CREATE INDEX IF NOT EXISTS "CommentScrapeJob_status_idx" ON "CommentScrapeJob"("status")')
            cur.execute('CREATE INDEX IF NOT EXISTS "CommentScrapeJob_audienceRoomId_idx" ON "CommentScrapeJob"("audienceRoomId")')
            cur.execute('CREATE INDEX IF NOT EXISTS "CommentScrapeJob_createdAt_idx" ON "CommentScrapeJob"("createdAt")')


def create_comment_scrape_job(
    audience_room_id: str,
    run_ids: List[str],
    enterprise_name: Optional[str] = None,
) -> CommentScrapeJob:
    ensure_comment_scrape_job_table_exists(enterprise_name)
    job_id = str(uuid.uuid4())
    now = datetime.utcnow()
    result = {"run_ids": run_ids, "batches_total": len(run_ids), "batches_completed": 0, "final_result": None}
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO "CommentScrapeJob" (id, "audienceRoomId", "enterpriseName", status, result, "createdAt", "updatedAt")
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (job_id, audience_room_id, enterprise_name, 'PROCESSING', Json(result), now, now)
            )
            row = cur.fetchone()
            return CommentScrapeJob(row)


def find_comment_scrape_job_by_id(job_id: str, enterprise_name: Optional[str] = None) -> Optional[CommentScrapeJob]:
    ensure_comment_scrape_job_table_exists(enterprise_name)
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT * FROM "CommentScrapeJob" WHERE id = %s', (job_id,))
            row = cur.fetchone()
            return CommentScrapeJob(row) if row else None


def find_latest_comment_scrape_job_by_audience_room_id(
    audience_room_id: str,
    enterprise_name: Optional[str] = None,
) -> Optional[CommentScrapeJob]:
    ensure_comment_scrape_job_table_exists(enterprise_name)
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                'SELECT * FROM "CommentScrapeJob" WHERE "audienceRoomId" = %s ORDER BY "createdAt" DESC LIMIT 1',
                (audience_room_id,)
            )
            row = cur.fetchone()
            return CommentScrapeJob(row) if row else None


def update_comment_scrape_job(
    job_id: str,
    data: Dict[str, Any],
    enterprise_name: Optional[str] = None,
) -> Optional[CommentScrapeJob]:
    ensure_comment_scrape_job_table_exists(enterprise_name)
    if not data:
        return find_comment_scrape_job_by_id(job_id, enterprise_name=enterprise_name)
    set_clauses = []
    values = []
    field_mapping = {'status': '"status"', 'result': '"result"', 'error': '"error"'}
    for key, value in data.items():
        if key in field_mapping:
            set_clauses.append(f'{field_mapping[key]} = %s')
            values.append(Json(value) if key == 'result' and value is not None else value)
    set_clauses.append('"updatedAt" = %s')
    values.append(datetime.utcnow())
    values.append(job_id)
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f'UPDATE "CommentScrapeJob" SET {", ".join(set_clauses)} WHERE id = %s RETURNING *',
                values
            )
            row = cur.fetchone()
            return CommentScrapeJob(row) if row else None
