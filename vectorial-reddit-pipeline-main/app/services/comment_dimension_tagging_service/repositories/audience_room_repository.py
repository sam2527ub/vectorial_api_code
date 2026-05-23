"""Repository for accessing audience room data for comment dimension tagging."""
import asyncio
from typing import Optional
from psycopg2.extras import RealDictCursor
from app.database.database_connection_pool_manager import get_enterprise_audience_connection
from app.database.models.audience_room import AudienceRoom
from app.database.models.audience_profile import AudienceProfile


async def find_audience_room_with_category(
    room_id: str,
    enterprise_name: Optional[str] = None,
    include_profiles: bool = True
) -> Optional[AudienceRoom]:
    """Find an AudienceRoom by ID and include category field (async)."""
    def _fetch_room():
        """Synchronous function to fetch room from database."""
        with get_enterprise_audience_connection(enterprise_name) as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Use SELECT * so query works even when category column is not yet in schema
                cur.execute(
                    'SELECT * FROM "AudienceRoom" WHERE id = %s',
                    (room_id,)
                )
                row = cur.fetchone()
                if not row:
                    return None
                
                room = AudienceRoom(row)
                
                # Add category field if it exists in schema; otherwise None (handler will use b2b default)
                if 'category' in row and row['category'] is not None:
                    room.category = row['category']
                elif 'Category' in row and row['Category'] is not None:
                    room.category = row['Category']
                else:
                    room.category = None
                
                if include_profiles:
                    cur.execute(
                        'SELECT * FROM "AudienceProfile" WHERE "audienceRoomId" = %s',
                        (room_id,)
                    )
                    profile_rows = cur.fetchall()
                    room.profiles = [AudienceProfile(r) for r in profile_rows]
                
                return room
    
    # Run database call in thread pool
    return await asyncio.to_thread(_fetch_room)
