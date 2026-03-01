"""Repository for SummariesJob (audience DB). Uses get_enterprise_audience_connection."""
from typing import Any, Dict, Optional

from app.config import logger
from app.database.connection import get_enterprise_audience_connection


def ensure_summaries_job_table_exists(enterprise_name: Optional[str] = None) -> None:
    """Create SummariesJob table if it doesn't exist. Safe - won't affect existing data."""
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS "SummariesJob" (
                    "id" TEXT NOT NULL,
                    "status" TEXT NOT NULL DEFAULT 'PENDING',
                    "audienceRoomId" TEXT NOT NULL,
                    "totalProfiles" INTEGER NOT NULL DEFAULT 0,
                    "processedProfiles" INTEGER NOT NULL DEFAULT 0,
                    "successCount" INTEGER NOT NULL DEFAULT 0,
                    "skippedCount" INTEGER NOT NULL DEFAULT 0,
                    "errorCount" INTEGER NOT NULL DEFAULT 0,
                    "currentChunk" INTEGER NOT NULL DEFAULT 0,
                    "totalChunks" INTEGER NOT NULL DEFAULT 0,
                    "error" TEXT,
                    "taskToken" TEXT,
                    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    "updatedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT "SummariesJob_pkey" PRIMARY KEY ("id")
                )
            """)
        with conn.cursor() as cur:
            cur.execute('CREATE INDEX IF NOT EXISTS "SummariesJob_status_idx" ON "SummariesJob"("status")')
        with conn.cursor() as cur:
            cur.execute('CREATE INDEX IF NOT EXISTS "SummariesJob_audienceRoomId_idx" ON "SummariesJob"("audienceRoomId")')
    logger.info("SummariesJob table ensured to exist")


def create_summaries_job(
    job_id: str,
    audience_room_id: str,
    task_token: Optional[str] = None,
    enterprise_name: Optional[str] = None
) -> Dict[str, Any]:
    """Create a new summaries job in the database."""
    ensure_summaries_job_table_exists(enterprise_name)

    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO "SummariesJob"
                (id, status, "audienceRoomId", "taskToken", "createdAt", "updatedAt")
                VALUES (%s, %s, %s, %s, NOW(), NOW())
                RETURNING id, status, "audienceRoomId", "totalProfiles",
                          "processedProfiles", "successCount", "skippedCount", "errorCount",
                          "currentChunk", "totalChunks", error, "taskToken",
                          "createdAt", "updatedAt"
            """, (job_id, 'PENDING', audience_room_id, task_token))
            row = cur.fetchone()
            return {
                "job_id": row[0],
                "status": row[1],
                "audience_room_id": row[2],
                "total_profiles": row[3],
                "processed_profiles": row[4],
                "success_count": row[5],
                "skipped_count": row[6],
                "error_count": row[7],
                "current_chunk": row[8],
                "total_chunks": row[9],
                "error": row[10],
                "task_token": row[11],
                "created_at": row[12].isoformat() if row[12] else None,
                "updated_at": row[13].isoformat() if row[13] else None
            }


def get_summaries_job(job_id: str, enterprise_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Get a summaries job from the database."""
    ensure_summaries_job_table_exists(enterprise_name)

    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, status, "audienceRoomId", "totalProfiles",
                       "processedProfiles", "successCount", "skippedCount", "errorCount",
                       "currentChunk", "totalChunks", error, "taskToken",
                       "createdAt", "updatedAt"
                FROM "SummariesJob"
                WHERE id = %s
            """, (job_id,))
            row = cur.fetchone()
            if not row:
                return None
            return {
                "job_id": row[0],
                "status": row[1],
                "audience_room_id": row[2],
                "total_profiles": row[3],
                "processed_profiles": row[4],
                "success_count": row[5],
                "skipped_count": row[6],
                "error_count": row[7],
                "current_chunk": row[8],
                "total_chunks": row[9],
                "error": row[10],
                "task_token": row[11],
                "created_at": row[12].isoformat() if row[12] else None,
                "updated_at": row[13].isoformat() if row[13] else None
            }


def update_summaries_job(
    job_id: str,
    enterprise_name: Optional[str] = None,
    **kwargs
) -> None:
    """Update a summaries job in the database."""
    ensure_summaries_job_table_exists(enterprise_name)

    set_clauses = ['"updatedAt" = NOW()']
    values = []

    field_mapping = {
        'status': 'status',
        'total_profiles': '"totalProfiles"',
        'processed_profiles': '"processedProfiles"',
        'success_count': '"successCount"',
        'skipped_count': '"skippedCount"',
        'error_count': '"errorCount"',
        'current_chunk': '"currentChunk"',
        'total_chunks': '"totalChunks"',
        'error': 'error'
    }

    for key, db_field in field_mapping.items():
        if key in kwargs:
            set_clauses.append(f"{db_field} = %s")
            values.append(kwargs[key])

    values.append(job_id)

    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                UPDATE "SummariesJob"
                SET {', '.join(set_clauses)}
                WHERE id = %s
            """, values)
