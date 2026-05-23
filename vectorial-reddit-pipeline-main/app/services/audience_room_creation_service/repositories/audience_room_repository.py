"""Room repository for audience room creation service."""
import uuid
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any
from psycopg2.extras import RealDictCursor
from app.services.audience_room_creation_service.clients.database.factory import get_database_client
from app.database.models.audience_room import AudienceRoom
from app.database.models.audience_profile import AudienceProfile

logger = logging.getLogger(__name__)


def create_audience_room(
    room_id: str,
    name: str,
    description_s3_url: str,
    user_id: Optional[str] = None,
    source: Optional[str] = None,
    query: Optional[str] = None,
    indexes_s3_url: Optional[str] = None,
    profiles_data: Optional[List[Dict[str, Any]]] = None,
    enterprise_name: Optional[str] = None,
    category: Optional[str] = None
) -> AudienceRoom:
    """Create a new AudienceRoom with optional profiles. category (b2b/b2c) is stored in existing DB column."""
    import sys
    now = datetime.utcnow()
    BATCH_SIZE = 20  # Insert profiles in batches of 20
    
    logger.info(f"DB: Starting create_audience_room for {room_id}, enterprise={enterprise_name}, category={category}")
    sys.stdout.flush()
    sys.stderr.flush()
    
    # Step 1: Create the room first (separate transaction)
    logger.info(f"DB: Step 1 - Creating room record...")
    sys.stdout.flush()
    
    db_client = get_database_client()
    try:
        with db_client.get_connection(enterprise_name) as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    INSERT INTO "AudienceRoom" (id, name, "descriptionS3Url", "userId", "source", "query", "indexesS3Url", "createdAt", "updatedAt", category)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (room_id, name, description_s3_url, user_id, source, query, indexes_s3_url, now, now, category)
                )
                room_row = cur.fetchone()
                room = AudienceRoom(room_row)
        
        logger.info(f"DB: Step 1 COMPLETE - Room {room_id} created successfully")
        sys.stdout.flush()
    except Exception as e:
        logger.error(f"DB: Step 1 FAILED - Error creating room: {type(e).__name__}: {e}")
        sys.stdout.flush()
        raise
    
    logger.info(f"DB: Step 2 - Inserting {len(profiles_data) if profiles_data else 0} profiles in batches of {BATCH_SIZE}")
    sys.stdout.flush()
    
    # Step 2: Insert profiles in batches (separate transactions per batch)
    if profiles_data:
        total_profiles = len(profiles_data)
        total_batches = (total_profiles + BATCH_SIZE - 1) // BATCH_SIZE
        
        for batch_start in range(0, total_profiles, BATCH_SIZE):
            batch_end = min(batch_start + BATCH_SIZE, total_profiles)
            batch = profiles_data[batch_start:batch_end]
            batch_num = batch_start // BATCH_SIZE + 1
            
            logger.info(f"DB: Batch {batch_num}/{total_batches} - Starting ({batch_end - batch_start} profiles)...")
            sys.stdout.flush()
            
            try:
                with db_client.get_connection(enterprise_name) as conn:
                    with conn.cursor(cursor_factory=RealDictCursor) as cur:
                        for profile_data in batch:
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
                            room.profiles.append(AudienceProfile(profile_row))
                
                logger.info(f"DB: Batch {batch_num}/{total_batches} - COMPLETE ({batch_end}/{total_profiles} profiles done)")
                sys.stdout.flush()
            except Exception as e:
                logger.error(f"DB: Batch {batch_num}/{total_batches} - FAILED: {type(e).__name__}: {e}")
                sys.stdout.flush()
                raise
    
    logger.info(f"DB: ALL DONE - Room {room_id} created with {len(room.profiles)} profiles")
    sys.stdout.flush()
    
    return room
