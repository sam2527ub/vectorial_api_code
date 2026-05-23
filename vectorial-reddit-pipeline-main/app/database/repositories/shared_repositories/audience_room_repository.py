"""Shared audience room repository - common operations used by multiple services."""
from datetime import datetime
from typing import Optional, Dict, Any
from psycopg2.extras import RealDictCursor
from app.database.database_connection_pool_manager import get_enterprise_audience_connection
from app.database.models.audience_room import AudienceRoom
from app.database.models.audience_profile import AudienceProfile


def find_audience_room_by_id(
    room_id: str,
    include_profiles: bool = False,
    enterprise_name: Optional[str] = None
) -> Optional[AudienceRoom]:
    """Find an AudienceRoom by ID, optionally including profiles."""
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT * FROM "AudienceRoom" WHERE id = %s', (room_id,))
            row = cur.fetchone()
            if not row:
                return None
            
            room = AudienceRoom(row)
            
            if include_profiles:
                cur.execute(
                    'SELECT * FROM "AudienceProfile" WHERE "audienceRoomId" = %s',
                    (room_id,)
                )
                profile_rows = cur.fetchall()
                room.profiles = [AudienceProfile(r) for r in profile_rows]
            
            return room


def update_audience_room(
    room_id: str,
    data: Dict[str, Any],
    enterprise_name: Optional[str] = None
) -> Optional[AudienceRoom]:
    """Update an AudienceRoom record."""
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
            query = f'UPDATE "AudienceRoom" SET {", ".join(set_clauses)} WHERE id = %s RETURNING *'
            cur.execute(query, values)
            row = cur.fetchone()
            return AudienceRoom(row) if row else None
