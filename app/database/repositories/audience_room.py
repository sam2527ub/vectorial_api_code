"""AudienceRoom repository - audience database."""
import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any

from psycopg2.extras import RealDictCursor

from app.database.pool import get_enterprise_audience_connection
from app.database.models import AudienceRoom, AudienceProfile


def create_audience_room(
    room_id: str,
    name: str,
    description_s3_url: str,
    user_id: Optional[str] = None,
    source: Optional[str] = None,
    query: Optional[str] = None,
    indexes_s3_url: Optional[str] = None,
    profiles_data: Optional[List[Dict[str, Any]]] = None,
    enterprise_name: Optional[str] = None
) -> AudienceRoom:
    now = datetime.utcnow()
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO "AudienceRoom" (id, name, "descriptionS3Url", "userId", "source", "query", "indexesS3Url", "createdAt", "updatedAt")
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (room_id, name, description_s3_url, user_id, source, query, indexes_s3_url, now, now)
            )
            room_row = cur.fetchone()
            if not room_row:
                raise Exception(f"Failed to create AudienceRoom {room_id}: INSERT returned no row")
            room = AudienceRoom(room_row)
            if profiles_data:
                for profile_data in profiles_data:
                    profile_id = profile_data.get('id', str(uuid.uuid4()))
                    cur.execute(
                        """
                        INSERT INTO "AudienceProfile"
                        (id, "audienceRoomId", "profileName", "profileUrl", "profileDescriptionS3Url", "postsS3Url", "commentsS3Url", "source", "createdAt", "updatedAt")
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING *
                        """,
                        (
                            profile_id,
                            room_id,
                            profile_data.get('profileName'),
                            profile_data.get('profileUrl') or profile_data.get('linkedinUrl'),
                            profile_data.get('profileDescriptionS3Url'),
                            profile_data.get('postsS3Url'),
                            profile_data.get('commentsS3Url'),
                            profile_data.get('source'),
                            now,
                            now
                        )
                    )
                    profile_row = cur.fetchone()
                    if profile_row:
                        room.profiles.append(AudienceProfile(profile_row))
            return room


def find_audience_room_by_id(
    room_id: str,
    include_profiles: bool = False,
    enterprise_name: Optional[str] = None
) -> Optional[AudienceRoom]:
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT * FROM "AudienceRoom" WHERE id = %s', (room_id,))
            row = cur.fetchone()
            if not row:
                return None
            room = AudienceRoom(row)
            if include_profiles:
                cur.execute('SELECT * FROM "AudienceProfile" WHERE "audienceRoomId" = %s', (room_id,))
                room.profiles = [AudienceProfile(r) for r in cur.fetchall()]
            return room


def get_user_id_from_enterprise(enterprise_name: Optional[str] = None) -> Optional[str]:
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT "userId" FROM "AudienceRoom" WHERE "userId" IS NOT NULL LIMIT 1')
            row = cur.fetchone()
    if not row:
        return None
    return row.get('userId') or row.get('userid')


def update_audience_room(
    room_id: str,
    data: Dict[str, Any],
    enterprise_name: Optional[str] = None
) -> Optional[AudienceRoom]:
    if not data:
        return find_audience_room_by_id(room_id, enterprise_name=enterprise_name)
    set_clauses = []
    values = []
    field_mapping = {
        'name': '"name"',
        'descriptionS3Url': '"descriptionS3Url"',
        'userId': '"userId"',
        'source': '"source"',
        'query': '"query"',
        'indexesS3Url': '"indexesS3Url"',
    }
    for key, value in data.items():
        if key in field_mapping:
            set_clauses.append(f'{field_mapping[key]} = %s')
            values.append(value)
    set_clauses.append('"updatedAt" = %s')
    values.append(datetime.utcnow())
    values.append(room_id)
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f'UPDATE "AudienceRoom" SET {", ".join(set_clauses)} WHERE id = %s RETURNING *',
                values
            )
            row = cur.fetchone()
            return AudienceRoom(row) if row else None


def delete_audience_room(room_id: str, enterprise_name: Optional[str] = None) -> bool:
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor() as cur:
            cur.execute('DELETE FROM "AudienceRoom" WHERE id = %s', (room_id,))
            return cur.rowcount > 0


def upsert_audience_room(
    room_id: str,
    name: str,
    description_s3_url: str,
    user_id: Optional[str] = None,
    source: Optional[str] = None,
    query: Optional[str] = None,
    indexes_s3_url: Optional[str] = None,
    enterprise_name: Optional[str] = None,
) -> AudienceRoom:
    now = datetime.utcnow()
    sql = """
        INSERT INTO "AudienceRoom" (id, name, "descriptionS3Url", "userId", "source", "query", "indexesS3Url", "createdAt", "updatedAt")
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET
            name = EXCLUDED.name,
            "descriptionS3Url" = EXCLUDED."descriptionS3Url",
            "userId" = EXCLUDED."userId",
            "source" = EXCLUDED."source",
            "query" = EXCLUDED."query",
            "indexesS3Url" = EXCLUDED."indexesS3Url",
            "updatedAt" = EXCLUDED."updatedAt"
        RETURNING *
    """
    params = (room_id, name, description_s3_url, user_id, source, query, indexes_s3_url, now, now)
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
    if not row:
        raise RuntimeError(f"AudienceRoom upsert failed (id={room_id})")
    return AudienceRoom(row)
