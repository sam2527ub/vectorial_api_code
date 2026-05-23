"""AudienceProfile repository - shared operations."""
from datetime import datetime
from typing import Optional, List, Dict, Any
from psycopg2.extras import RealDictCursor
from app.database.database_connection_pool_manager import get_audience_connection, get_enterprise_audience_connection
from app.database.models.audience_profile import AudienceProfile
from app.database.models.audience_room import AudienceRoom


def find_audience_profiles(
    audience_room_id: Optional[str] = None,
    all_profiles: bool = False
) -> List[AudienceProfile]:
    """Find AudienceProfiles, optionally filtered by room ID."""
    with get_audience_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if audience_room_id:
                cur.execute(
                    'SELECT * FROM "AudienceProfile" WHERE "audienceRoomId" = %s',
                    (audience_room_id,)
                )
            elif all_profiles:
                cur.execute('SELECT * FROM "AudienceProfile"')
            else:
                return []
            
            rows = cur.fetchall()
            return [AudienceProfile(r) for r in rows]


def find_audience_profile_by_id(
    profile_id: str,
    include_room: bool = False,
    enterprise_name: Optional[str] = None
) -> Optional[AudienceProfile]:
    """Find an AudienceProfile by ID, optionally including the room."""
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT * FROM "AudienceProfile" WHERE id = %s', (profile_id,))
            row = cur.fetchone()
            if not row:
                return None
            
            profile = AudienceProfile(row)
            
            if include_room and profile.audienceRoomId:
                cur.execute(
                    'SELECT * FROM "AudienceRoom" WHERE id = %s',
                    (profile.audienceRoomId,)
                )
                room_row = cur.fetchone()
                if room_row:
                    profile.audienceRoom = AudienceRoom(room_row)
            
            return profile


def update_audience_profile(profile_id: str, data: Dict[str, Any]) -> Optional[AudienceProfile]:
    """Update an AudienceProfile record."""
    if not data:
        return find_audience_profile_by_id(profile_id)
    
    set_clauses = []
    values = []
    
    field_mapping = {
        'profileName': '"profileName"',
        'profileUrl': '"profileUrl"',
        'linkedinUrl': '"profileUrl"',  # Support old field name for backward compatibility
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
    
    with get_audience_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            query = f'UPDATE "AudienceProfile" SET {", ".join(set_clauses)} WHERE id = %s RETURNING *'
            cur.execute(query, values)
            row = cur.fetchone()
            return AudienceProfile(row) if row else None


def delete_audience_profiles_by_room(room_id: str, enterprise_name: Optional[str] = None) -> int:
    """Delete all profiles in an audience room."""
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor() as cur:
            cur.execute(
                'DELETE FROM "AudienceProfile" WHERE "audienceRoomId" = %s',
                (room_id,)
            )
            return cur.rowcount
