"""ScrapeJob repository - audience database."""
import uuid
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

from psycopg2.extras import RealDictCursor, Json

from app.database.pool import get_enterprise_audience_connection
from app.database.models import ScrapeJob

logger = logging.getLogger(__name__)


def create_scrape_job(
    linkedin_urls: List[str],
    max_posts: int,
    audience_room_id: Optional[str] = None,
    enterprise_name: Optional[str] = None
) -> ScrapeJob:
    job_id = str(uuid.uuid4())
    now = datetime.utcnow()
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO "ScrapeJob" (id, status, "linkedinUrls", "maxPosts", "audienceRoomId", "createdAt", "updatedAt")
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (job_id, 'PENDING', Json(linkedin_urls), max_posts, audience_room_id, now, now)
            )
            row = cur.fetchone()
            return ScrapeJob(row)


def find_scrape_job_by_id(job_id: str, enterprise_name: Optional[str] = None) -> Optional[ScrapeJob]:
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT * FROM "ScrapeJob" WHERE id = %s', (job_id,))
            row = cur.fetchone()
            return ScrapeJob(row) if row else None


def update_scrape_job(job_id: str, data: Dict[str, Any], enterprise_name: Optional[str] = None) -> Optional[ScrapeJob]:
    if not data:
        return find_scrape_job_by_id(job_id, enterprise_name=enterprise_name)
    set_clauses = []
    values = []
    field_mapping = {
        'status': '"status"',
        'apifyRunId': '"apifyRunId"',
        'result': '"result"',
        'error': '"error"',
        'audienceRoomId': '"audienceRoomId"',
    }
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
                f'UPDATE "ScrapeJob" SET {", ".join(set_clauses)} WHERE id = %s RETURNING *',
                values
            )
            row = cur.fetchone()
            return ScrapeJob(row) if row else None
