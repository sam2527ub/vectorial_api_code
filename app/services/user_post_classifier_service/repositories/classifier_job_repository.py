"""Repository for ClassifierJob (audience DB). Uses get_enterprise_audience_connection."""
from typing import Any, Dict, Optional

from app.config import logger
from app.database.connection import get_enterprise_audience_connection


def ensure_classifier_job_table_exists(enterprise_name: Optional[str] = None) -> None:
    """Create ClassifierJob table if it doesn't exist. Safe - won't affect existing data."""
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS "ClassifierJob" (
                    "id" TEXT NOT NULL,
                    "status" TEXT NOT NULL DEFAULT 'PENDING',
                    "classifierId" TEXT NOT NULL,
                    "audienceRoomId" TEXT NOT NULL,
                    "totalProfiles" INTEGER NOT NULL DEFAULT 0,
                    "processedProfiles" INTEGER NOT NULL DEFAULT 0,
                    "totalPostsClassified" INTEGER NOT NULL DEFAULT 0,
                    "error" TEXT,
                    "taskToken" TEXT,
                    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    "updatedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT "ClassifierJob_pkey" PRIMARY KEY ("id")
                )
            """)
        with conn.cursor() as cur:
            cur.execute('CREATE INDEX IF NOT EXISTS "ClassifierJob_status_idx" ON "ClassifierJob"("status")')
        with conn.cursor() as cur:
            cur.execute('CREATE INDEX IF NOT EXISTS "ClassifierJob_audienceRoomId_idx" ON "ClassifierJob"("audienceRoomId")')
        with conn.cursor() as cur:
            cur.execute('CREATE INDEX IF NOT EXISTS "ClassifierJob_classifierId_idx" ON "ClassifierJob"("classifierId")')
    logger.info("ClassifierJob table ensured to exist")


def create_classifier_job(
    job_id: str,
    classifier_id: str,
    audience_room_id: str,
    task_token: Optional[str] = None,
    enterprise_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new classifier job in the database."""
    ensure_classifier_job_table_exists(enterprise_name)
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO "ClassifierJob"
                (id, status, "classifierId", "audienceRoomId", "taskToken", "createdAt", "updatedAt")
                VALUES (%s, %s, %s, %s, %s, NOW(), NOW())
                RETURNING id, status, "classifierId", "audienceRoomId", "totalProfiles",
                          "processedProfiles", "totalPostsClassified", error, "taskToken",
                          "createdAt", "updatedAt"
            """, (job_id, 'PENDING', classifier_id, audience_room_id, task_token))
            row = cur.fetchone()
            return {
                "job_id": row[0],
                "status": row[1],
                "classifier_id": row[2],
                "audience_room_id": row[3],
                "total_profiles": row[4],
                "processed_profiles": row[5],
                "total_posts_classified": row[6],
                "error": row[7],
                "task_token": row[8],
                "created_at": row[9].isoformat() if row[9] else None,
                "updated_at": row[10].isoformat() if row[10] else None,
            }


def get_classifier_job(job_id: str, enterprise_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Get a classifier job from the database."""
    ensure_classifier_job_table_exists(enterprise_name)
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, status, "classifierId", "audienceRoomId", "totalProfiles",
                       "processedProfiles", "totalPostsClassified", error, "taskToken",
                       "createdAt", "updatedAt"
                FROM "ClassifierJob"
                WHERE id = %s
            """, (job_id,))
            row = cur.fetchone()
            if not row:
                return None
            return {
                "job_id": row[0],
                "status": row[1],
                "classifier_id": row[2],
                "audience_room_id": row[3],
                "total_profiles": row[4],
                "processed_profiles": row[5],
                "total_posts_classified": row[6],
                "error": row[7],
                "task_token": row[8],
                "created_at": row[9].isoformat() if row[9] else None,
                "updated_at": row[10].isoformat() if row[10] else None,
            }


def update_classifier_job(
    job_id: str,
    enterprise_name: Optional[str] = None,
    **kwargs,
) -> None:
    """Update a classifier job in the database."""
    ensure_classifier_job_table_exists(enterprise_name)
    set_clauses = ['"updatedAt" = NOW()']
    values = []
    field_mapping = {
        'status': 'status',
        'total_profiles': '"totalProfiles"',
        'processed_profiles': '"processedProfiles"',
        'total_posts_classified': '"totalPostsClassified"',
        'error': 'error',
    }
    for key, db_field in field_mapping.items():
        if key in kwargs:
            set_clauses.append(f"{db_field} = %s")
            values.append(kwargs[key])
    values.append(job_id)
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                UPDATE "ClassifierJob"
                SET {', '.join(set_clauses)}
                WHERE id = %s
            """, values)
