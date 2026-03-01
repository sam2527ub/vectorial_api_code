"""Preview repository - audience database."""
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

from psycopg2.extras import RealDictCursor, Json

from app.database.pool import get_enterprise_audience_connection, get_audience_connection

logger = logging.getLogger(__name__)


def find_all_previews(
    user_id: Optional[str] = None,
    enterprise_name: Optional[str] = None
) -> List[Dict[str, Any]]:
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if user_id:
                cur.execute('SELECT * FROM "previews" WHERE user_id = %s ORDER BY created_at DESC', (user_id,))
            else:
                cur.execute('SELECT * FROM "previews" ORDER BY created_at DESC')
            return [dict(row) for row in cur.fetchall()]


def find_preview_by_room_id(
    room_id: str,
    user_id: Optional[str] = None,
    enterprise_name: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if user_id:
                cur.execute('SELECT * FROM "previews" WHERE room_id = %s AND user_id = %s', (room_id, user_id))
            else:
                cur.execute('SELECT * FROM "previews" WHERE room_id = %s LIMIT 1', (room_id,))
            row = cur.fetchone()
            return dict(row) if row else None


def ensure_preview_table_exists(enterprise_name: Optional[str] = None) -> bool:
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS "previews" (
                    room_id VARCHAR(255) NOT NULL,
                    user_id VARCHAR(255) NOT NULL DEFAULT 'default',
                    name VARCHAR(500),
                    description_summary TEXT,
                    source VARCHAR(50),
                    total_profile_count INTEGER DEFAULT 0,
                    profiles JSONB,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW(),
                    PRIMARY KEY (room_id, user_id)
                )
            """)
            cur.execute("""
                DO $$ BEGIN
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='previews' AND column_name='source') THEN
                        ALTER TABLE "previews" ADD COLUMN source VARCHAR(50);
                    END IF;
                END $$;
            """)
            cur.execute("""
                DO $$ BEGIN
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='previews' AND column_name='total_profile_count') THEN
                        ALTER TABLE "previews" ADD COLUMN total_profile_count INTEGER DEFAULT 0;
                    END IF;
                END $$;
            """)
            cur.execute("""
                DO $$ BEGIN
                    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='previews' AND column_name='traits') THEN
                        ALTER TABLE "previews" DROP COLUMN traits;
                    END IF;
                END $$;
            """)
    return True


def upsert_preview(
    room_id: str,
    name: str,
    user_id: str = "default",
    description_summary: Optional[str] = None,
    source: Optional[str] = None,
    total_profile_count: int = 0,
    profiles: Optional[List[Dict[str, Any]]] = None,
    enterprise_name: Optional[str] = None
) -> Dict[str, Any]:
    now = datetime.utcnow()
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO "previews" (room_id, user_id, name, description_summary, source, total_profile_count, profiles, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (room_id, user_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    description_summary = EXCLUDED.description_summary,
                    source = EXCLUDED.source,
                    total_profile_count = EXCLUDED.total_profile_count,
                    profiles = EXCLUDED.profiles,
                    updated_at = EXCLUDED.updated_at
                RETURNING *
            """, (room_id, user_id, name, description_summary, source, total_profile_count, Json(profiles) if profiles else None, now, now))
            row = cur.fetchone()
            return dict(row) if row else {}


def delete_preview(
    room_id: str,
    user_id: Optional[str] = None,
    enterprise_name: Optional[str] = None
) -> bool:
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor() as cur:
            if user_id:
                cur.execute('DELETE FROM "previews" WHERE room_id = %s AND user_id = %s', (room_id, user_id))
            else:
                cur.execute('DELETE FROM "previews" WHERE room_id = %s', (room_id,))
            return cur.rowcount > 0


def update_preview_name(
    room_id: str,
    new_name: str,
    enterprise_name: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    now = datetime.utcnow()
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                'UPDATE "previews" SET name = %s, updated_at = %s WHERE room_id = %s RETURNING *',
                (new_name, now, room_id)
            )
            row = cur.fetchone()
            return dict(row) if row else None


def delete_orphaned_previews(enterprise_name: Optional[str] = None) -> Dict[str, int]:
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM "previews" p
                WHERE NOT EXISTS (SELECT 1 FROM "AudienceRoom" ar WHERE ar.id = p.room_id)
            """)
            orphaned_count = cur.rowcount
            cur.execute("""
                DELETE FROM "previews" p1
                WHERE EXISTS (
                    SELECT 1 FROM "previews" p2
                    WHERE p2.room_id = p1.room_id
                    AND (p2.updated_at > p1.updated_at
                         OR (p2.updated_at = p1.updated_at AND p2.created_at > p1.created_at)
                         OR (p2.updated_at = p1.updated_at AND p2.created_at = p1.created_at AND p2.user_id > p1.user_id))
                )
            """)
            duplicates_count = cur.rowcount
    return {"orphaned": orphaned_count, "duplicates": duplicates_count, "total": orphaned_count + duplicates_count}


def find_all_audience_rooms_with_profiles(
    limit: int = 5,
    enterprise_name: Optional[str] = None
) -> List[Dict[str, Any]]:
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT id, name, "descriptionS3Url", source, "userId", query, "createdAt" FROM "AudienceRoom" ORDER BY "createdAt" DESC')
            rooms = [dict(row) for row in cur.fetchall()]
            for room in rooms:
                cur.execute('SELECT COUNT(*) as count FROM "AudienceProfile" WHERE "audienceRoomId" = %s', (room['id'],))
                count_row = cur.fetchone()
                room['total_profile_count'] = count_row['count'] if count_row else 0
                cur.execute("""
                    SELECT id, "profileName", "profileUrl", "profileDescriptionS3Url", source
                    FROM "AudienceProfile" WHERE "audienceRoomId" = %s ORDER BY "createdAt" LIMIT %s
                """, (room['id'], limit))
                room['profiles'] = [dict(row) for row in cur.fetchall()]
            return rooms


def find_audience_room_with_profiles_for_preview(room_id: str, profile_limit: int = 5) -> Optional[Dict[str, Any]]:
    with get_audience_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT id, name, "descriptionS3Url", source, "userId", query, "createdAt" FROM "AudienceRoom" WHERE id = %s', (room_id,))
            room_row = cur.fetchone()
            if not room_row:
                return None
            room = dict(room_row)
            cur.execute('SELECT COUNT(*) as count FROM "AudienceProfile" WHERE "audienceRoomId" = %s', (room_id,))
            count_row = cur.fetchone()
            room['total_profile_count'] = count_row['count'] if count_row else 0
            cur.execute("""
                SELECT id, "profileName", "profileUrl", "profileDescriptionS3Url", source
                FROM "AudienceProfile" WHERE "audienceRoomId" = %s ORDER BY "createdAt" LIMIT %s
            """, (room_id, profile_limit))
            room['profiles'] = [dict(row) for row in cur.fetchall()]
            return room
