"""AudienceProfile repository - audience database."""
from datetime import datetime
from typing import Optional, List, Dict, Any

from psycopg2.extras import RealDictCursor

from app.database.pool import get_enterprise_audience_connection
from app.database.models import AudienceRoom, AudienceProfile


def find_audience_profiles(
    audience_room_id: Optional[str] = None,
    all_profiles: bool = False,
    enterprise_name: Optional[str] = None
) -> List[AudienceProfile]:
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if audience_room_id:
                cur.execute('SELECT * FROM "AudienceProfile" WHERE "audienceRoomId" = %s', (audience_room_id,))
            elif all_profiles:
                cur.execute('SELECT * FROM "AudienceProfile"')
            else:
                return []
            return [AudienceProfile(r) for r in cur.fetchall()]


def find_audience_profile_by_id(
    profile_id: str,
    include_room: bool = False,
    enterprise_name: Optional[str] = None
) -> Optional[AudienceProfile]:
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT * FROM "AudienceProfile" WHERE id = %s', (profile_id,))
            row = cur.fetchone()
            if not row:
                return None
            profile = AudienceProfile(row)
            if include_room and profile.audienceRoomId:
                cur.execute('SELECT * FROM "AudienceRoom" WHERE id = %s', (profile.audienceRoomId,))
                room_row = cur.fetchone()
                if room_row:
                    profile.audienceRoom = AudienceRoom(room_row)
            return profile


def update_audience_profile(
    profile_id: str,
    data: Dict[str, Any],
    enterprise_name: Optional[str] = None
) -> Optional[AudienceProfile]:
    if not data:
        return find_audience_profile_by_id(profile_id, enterprise_name=enterprise_name)
    set_clauses = []
    values = []
    field_mapping = {
        'profileName': '"profileName"',
        'profileUrl': '"profileUrl"',
        'linkedinUrl': '"profileUrl"',
        'profileDescriptionS3Url': '"profileDescriptionS3Url"',
        'postsS3Url': '"postsS3Url"',
        'commentsS3Url': '"commentsS3Url"',
        'source': '"source"',
    }
    for key, value in data.items():
        if key in field_mapping:
            set_clauses.append(f'{field_mapping[key]} = %s')
            values.append(value)
    set_clauses.append('"updatedAt" = %s')
    values.append(datetime.utcnow())
    values.append(profile_id)
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f'UPDATE "AudienceProfile" SET {", ".join(set_clauses)} WHERE id = %s RETURNING *',
                values
            )
            row = cur.fetchone()
            return AudienceProfile(row) if row else None


def delete_audience_profiles_by_room(room_id: str, enterprise_name: Optional[str] = None) -> int:
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor() as cur:
            cur.execute('DELETE FROM "AudienceProfile" WHERE "audienceRoomId" = %s', (room_id,))
            return cur.rowcount


def delete_audience_profile(profile_id: str, enterprise_name: Optional[str] = None) -> bool:
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor() as cur:
            cur.execute('DELETE FROM "AudienceProfile" WHERE id = %s', (profile_id,))
            return cur.rowcount > 0


def upsert_audience_profile(
    profile_id: str,
    audience_room_id: str,
    profile_name: str,
    profile_url: str,
    profile_description_s3_url: Optional[str] = None,
    posts_s3_url: Optional[str] = None,
    comments_s3_url: Optional[str] = None,
    source: Optional[str] = None,
    enterprise_name: Optional[str] = None,
) -> AudienceProfile:
    now = datetime.utcnow()
    sql = """
        INSERT INTO "AudienceProfile" (id, "audienceRoomId", "profileName", "profileUrl", "profileDescriptionS3Url", "postsS3Url", "commentsS3Url", "source", "createdAt", "updatedAt")
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET
            "audienceRoomId" = EXCLUDED."audienceRoomId",
            "profileName" = EXCLUDED."profileName",
            "profileUrl" = EXCLUDED."profileUrl",
            "profileDescriptionS3Url" = EXCLUDED."profileDescriptionS3Url",
            "postsS3Url" = EXCLUDED."postsS3Url",
            "commentsS3Url" = EXCLUDED."commentsS3Url",
            "source" = EXCLUDED."source",
            "updatedAt" = EXCLUDED."updatedAt"
        RETURNING *
    """
    params = (profile_id, audience_room_id, profile_name, profile_url, profile_description_s3_url, posts_s3_url, comments_s3_url, source, now, now)
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
    if not row:
        raise RuntimeError(f"AudienceProfile upsert failed (id={profile_id})")
    return AudienceProfile(row)
