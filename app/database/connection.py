"""
Database utility module using psycopg2 for PostgreSQL operations.
Replaces Prisma client to avoid deployment issues on Vercel.
"""

import os
import json
import logging
import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor, Json
from psycopg2.pool import ThreadedConnectionPool

logger = logging.getLogger(__name__)

# Connection pools for both databases
_main_pool: Optional[ThreadedConnectionPool] = None
_audience_pool: Optional[ThreadedConnectionPool] = None
_gamma_pool: Optional[ThreadedConnectionPool] = None
_app_pool: Optional[ThreadedConnectionPool] = None
_entelligence_pool: Optional[ThreadedConnectionPool] = None
_beta_pool: Optional[ThreadedConnectionPool] = None


def get_main_pool() -> Optional[ThreadedConnectionPool]:
    """Get or create the main database connection pool."""
    global _main_pool
    if _main_pool is None:
        database_url = os.getenv("DATABASE_URL")
        if database_url:
            try:
                _main_pool = ThreadedConnectionPool(1, 10, database_url)
                logger.info("Main database pool initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize main database pool: {e}")
    return _main_pool


def get_audience_pool() -> Optional[ThreadedConnectionPool]:
    """Get or create the audience database connection pool."""
    global _audience_pool
    if _audience_pool is None:
        database_url = os.getenv("AUDIENCE_DATABASE_URL")
        if database_url:
            try:
                _audience_pool = ThreadedConnectionPool(1, 10, database_url)
                logger.info("Audience database pool initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize audience database pool: {e}")
    return _audience_pool


def get_gamma_pool() -> Optional[ThreadedConnectionPool]:
    """Get or create the gamma database connection pool."""
    global _gamma_pool
    if _gamma_pool is None:
        database_url = os.getenv("GAMMA_DATABASE_URL")
        if database_url:
            try:
                _gamma_pool = ThreadedConnectionPool(1, 10, database_url)
                logger.info("Gamma database pool initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize gamma database pool: {e}")
    return _gamma_pool


def get_app_pool() -> Optional[ThreadedConnectionPool]:
    """Get or create the app database connection pool."""
    global _app_pool
    if _app_pool is None:
        database_url = os.getenv("APP_DATABASE_URL")
        if database_url:
            try:
                _app_pool = ThreadedConnectionPool(1, 10, database_url)
                logger.info("App database pool initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize app database pool: {e}")
    return _app_pool


def get_entelligence_pool() -> Optional[ThreadedConnectionPool]:
    """Get or create the entelligence database connection pool."""
    global _entelligence_pool
    if _entelligence_pool is None:
        database_url = os.getenv("ENTELLIGENCE_DATABASE_URL")
        if database_url:
            try:
                _entelligence_pool = ThreadedConnectionPool(1, 10, database_url)
                logger.info("Entelligence database pool initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize entelligence database pool: {e}")
    return _entelligence_pool


def get_beta_pool() -> Optional[ThreadedConnectionPool]:
    """Get or create the beta database connection pool."""
    global _beta_pool
    if _beta_pool is None:
        database_url = os.getenv("BETA_DATABASE_URL")
        if database_url:
            try:
                _beta_pool = ThreadedConnectionPool(1, 10, database_url)
                logger.info("Beta database pool initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize beta database pool: {e}")
    return _beta_pool


@contextmanager
def get_main_connection():
    """Context manager for main database connections."""
    pool = get_main_pool()
    if not pool:
        raise Exception("Main database pool not available. Please set DATABASE_URL.")
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        pool.putconn(conn)


@contextmanager
def get_audience_connection():
    """Context manager for audience database connections."""
    pool = get_audience_pool()
    if not pool:
        raise Exception("Audience database pool not available. Please set AUDIENCE_DATABASE_URL.")
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        pool.putconn(conn)


@contextmanager
def get_enterprise_audience_connection(enterprise_name: Optional[str] = None):
    """Context manager for enterprise-specific audience database connections.
    
    Args:
        enterprise_name: Optional enterprise name. If provided, must be one of:
            - "gamma" -> uses GAMMA_DATABASE_URL
            - "app" -> uses APP_DATABASE_URL
            - "entelligence" -> uses ENTELLIGENCE_DATABASE_URL
            - "beta" -> uses BETA_DATABASE_URL
        If None or not provided, defaults to AUDIENCE_DATABASE_URL
    """
    pool = None
    
    # Normalize enterprise name: lowercase and strip whitespace
    normalized_enterprise = enterprise_name.lower().strip() if enterprise_name else None
    logger.info(f"get_enterprise_audience_connection called with enterprise_name='{enterprise_name}', normalized='{normalized_enterprise}'")
    
    if normalized_enterprise == "gamma":
        pool = get_gamma_pool()
        if not pool:
            raise Exception("Gamma database pool not available. Please set GAMMA_DATABASE_URL.")
    elif normalized_enterprise == "app":
        pool = get_app_pool()
        if not pool:
            raise Exception("App database pool not available. Please set APP_DATABASE_URL.")
    elif normalized_enterprise == "entelligence":
        pool = get_entelligence_pool()
        if not pool:
            raise Exception("Entelligence database pool not available. Please set ENTELLIGENCE_DATABASE_URL.")
    elif normalized_enterprise == "beta":
        logger.info("Using BETA database connection")
        pool = get_beta_pool()
        if not pool:
            raise Exception("Beta database pool not available. Please set BETA_DATABASE_URL.")
    else:
        # Default to audience database
        logger.info(f"Using default AUDIENCE database connection (enterprise_name was '{enterprise_name}', normalized='{normalized_enterprise}')")
        pool = get_audience_pool()
        if not pool:
            raise Exception("Audience database pool not available. Please set AUDIENCE_DATABASE_URL.")
    
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        pool.putconn(conn)


def close_pools():
    """Close all connection pools."""
    global _main_pool, _audience_pool, _gamma_pool, _app_pool, _entelligence_pool, _beta_pool
    if _main_pool:
        _main_pool.closeall()
        _main_pool = None
        logger.info("Main database pool closed")
    if _audience_pool:
        _audience_pool.closeall()
        _audience_pool = None
        logger.info("Audience database pool closed")
    if _gamma_pool:
        _gamma_pool.closeall()
        _gamma_pool = None
        logger.info("Gamma database pool closed")
    if _app_pool:
        _app_pool.closeall()
        _app_pool = None
        logger.info("App database pool closed")
    if _entelligence_pool:
        _entelligence_pool.closeall()
        _entelligence_pool = None
        logger.info("Entelligence database pool closed")
    if _beta_pool:
        _beta_pool.closeall()
        _beta_pool = None
        logger.info("Beta database pool closed")


# ============================================
# Data Classes for Results (replacing Prisma models)
# ============================================

class ScrapeJob:
    """Represents a ScrapeJob record."""
    def __init__(self, row: Dict[str, Any]):
        self.id = row.get('id')
        self.status = row.get('status', 'PENDING')
        self.linkedinUrls = row.get('linkedinUrls') or row.get('linkedinurls')
        self.maxPosts = row.get('maxPosts') or row.get('maxposts')
        self.apifyRunId = row.get('apifyRunId') or row.get('apifyrunid')
        self.result = row.get('result')
        self.error = row.get('error')
        self.audienceRoomId = row.get('audienceRoomId') or row.get('audienceroomid')
        self.createdAt = row.get('createdAt') or row.get('createdat')
        self.updatedAt = row.get('updatedAt') or row.get('updatedat')


class AudienceRoom:
    """Represents an AudienceRoom record."""
    def __init__(self, row: Dict[str, Any]):
        self.id = row.get('id')
        self.name = row.get('name')
        self.descriptionS3Url = row.get('descriptionS3Url') or row.get('descriptions3url')
        self.source = row.get('source')
        self.query = row.get('query')
        self.indexesS3Url = row.get('indexesS3Url') or row.get('indexess3url')
        self.userId = row.get('userId') or row.get('userid')
        self.createdAt = row.get('createdAt') or row.get('createdat')
        self.updatedAt = row.get('updatedAt') or row.get('updatedat')
        self.profiles: List['AudienceProfile'] = []


class AudienceProfile:
    """Represents an AudienceProfile record."""
    def __init__(self, row: Dict[str, Any]):
        self.id = row.get('id')
        self.audienceRoomId = row.get('audienceRoomId') or row.get('audienceroomid')
        self.profileName = row.get('profileName') or row.get('profilename')
        # Support both old and new column names for backward compatibility
        self.profileUrl = row.get('profileUrl') or row.get('profileurl') or row.get('linkedinUrl') or row.get('linkedinurl')
        self.profileDescriptionS3Url = row.get('profileDescriptionS3Url') or row.get('profiledescriptions3url')
        self.postsS3Url = row.get('postsS3Url') or row.get('postss3url')
        self.commentsS3Url = row.get('commentsS3Url') or row.get('commentss3url')
        self.source = row.get('source')
        self.createdAt = row.get('createdAt') or row.get('createdat')
        self.updatedAt = row.get('updatedAt') or row.get('updatedat')
        self.audienceRoom: Optional[AudienceRoom] = None


class PostClassifier:
    """Represents a PostClassifier record."""
    def __init__(self, row: Dict[str, Any]):
        self.id = row.get('id')
        self.userId = row.get('userId') or row.get('userid')
        self.name = row.get('name')
        self.description = row.get('description')
        self.prompt = row.get('prompt')
        # Handle labels - could be stored as array or JSON
        labels_raw = row.get('labels')
        if isinstance(labels_raw, str):
            try:
                self.labels = json.loads(labels_raw)
            except json.JSONDecodeError:
                self.labels = [labels_raw] if labels_raw else []
        elif isinstance(labels_raw, list):
            self.labels = labels_raw
        else:
            self.labels = []
        # Handle examples - could be JSON
        examples_raw = row.get('examples')
        if isinstance(examples_raw, str):
            try:
                self.examples = json.loads(examples_raw)
            except json.JSONDecodeError:
                self.examples = None
        else:
            self.examples = examples_raw
        self.createdAt = row.get('createdAt') or row.get('createdat')
        self.updatedAt = row.get('updatedAt') or row.get('updatedat')


# ============================================
# ScrapeJob Operations (Audience Database)
# ============================================

def create_scrape_job(
    linkedin_urls: List[str],
    max_posts: int,
    audience_room_id: Optional[str] = None,
    enterprise_name: Optional[str] = None
) -> ScrapeJob:
    """Create a new ScrapeJob record.
    
    Args:
        enterprise_name: Optional enterprise name (gamma, app, entelligence, beta). 
                        Defaults to AUDIENCE_DATABASE_URL if None.
    """
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
    """Find a ScrapeJob by ID.
    
    Args:
        job_id: The scrape job ID
        enterprise_name: Optional enterprise name (gamma, app, entelligence, beta). 
                        Defaults to AUDIENCE_DATABASE_URL if None.
    """
    logger.info(f"find_scrape_job_by_id called with job_id={job_id}, enterprise_name={enterprise_name}")
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT * FROM "ScrapeJob" WHERE id = %s', (job_id,))
            row = cur.fetchone()
            if row:
                logger.info(f"Found ScrapeJob {job_id} in database (enterprise_name={enterprise_name})")
            else:
                logger.warning(f"ScrapeJob {job_id} not found in database (enterprise_name={enterprise_name})")
            return ScrapeJob(row) if row else None


def update_scrape_job(job_id: str, data: Dict[str, Any], enterprise_name: Optional[str] = None) -> Optional[ScrapeJob]:
    """Update a ScrapeJob record.
    
    Args:
        job_id: The scrape job ID to update
        data: Dictionary of fields to update
        enterprise_name: Optional enterprise name (gamma, app, entelligence, beta). 
                        Defaults to AUDIENCE_DATABASE_URL if None.
    """
    logger.info(f"update_scrape_job called: job_id={job_id}, enterprise_name={enterprise_name}, data_keys={list(data.keys())}")
    
    if not data:
        return find_scrape_job_by_id(job_id, enterprise_name=enterprise_name)
    
    # Build update query dynamically
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
            # Handle JSON fields
            if key == 'result':
                values.append(Json(value) if value is not None else None)
            else:
                values.append(value)
    
    # Always update updatedAt
    set_clauses.append('"updatedAt" = %s')
    values.append(datetime.utcnow())
    
    # Add job_id for WHERE clause
    values.append(job_id)
    
    try:
        with get_enterprise_audience_connection(enterprise_name) as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                query = f'UPDATE "ScrapeJob" SET {", ".join(set_clauses)} WHERE id = %s RETURNING *'
                logger.info(f"Executing update query for job {job_id} (enterprise_name={enterprise_name}): UPDATE ScrapeJob SET {', '.join([f.split('=')[0].strip() for f in set_clauses])} WHERE id = ...")
                cur.execute(query, values)
                row = cur.fetchone()
                if row:
                    logger.info(f"Successfully updated job {job_id} in database (enterprise_name={enterprise_name})")
                else:
                    logger.warning(f"UPDATE query returned no rows for job {job_id} - job may not exist in database (enterprise_name={enterprise_name})")
                return ScrapeJob(row) if row else None
    except Exception as e:
        logger.error(f"Exception in update_scrape_job for job {job_id} (enterprise_name={enterprise_name}): {e}", exc_info=True)
        raise


# ============================================
# AudienceRoom Operations (Audience Database)
# ============================================

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
    """Create a new AudienceRoom with optional profiles.
    
    Args:
        enterprise_name: Optional enterprise name (gamma, app, entelligence, beta). 
                        Defaults to AUDIENCE_DATABASE_URL if None.
    """
    now = datetime.utcnow()
    
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Create the room
            cur.execute(
                """
                INSERT INTO "AudienceRoom" (id, name, "descriptionS3Url", "userId", "source", "query", "indexesS3Url", "createdAt", "updatedAt")
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (room_id, name, description_s3_url, user_id, source, query, indexes_s3_url, now, now)
            )
            room_row = cur.fetchone()
            room = AudienceRoom(room_row)
            
            # Create profiles if provided
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
                            profile_data.get('profileUrl') or profile_data.get('linkedinUrl'),  # Support both old and new field names
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
            
            return room


def find_audience_room_by_id(room_id: str, include_profiles: bool = False, enterprise_name: Optional[str] = None) -> Optional[AudienceRoom]:
    """Find an AudienceRoom by ID, optionally including profiles.
    
    Args:
        room_id: The audience room ID
        include_profiles: Whether to include associated profiles
        enterprise_name: Optional enterprise name (gamma, app, entelligence, beta). Defaults to AUDIENCE_DATABASE_URL if None.
    """
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


def update_audience_room(room_id: str, data: Dict[str, Any], enterprise_name: Optional[str] = None) -> Optional[AudienceRoom]:
    """Update an AudienceRoom record.
    
    Args:
        room_id: The audience room ID to update
        data: Dictionary of fields to update
        enterprise_name: Optional enterprise name (gamma, app, entelligence, beta). 
                        Defaults to AUDIENCE_DATABASE_URL if None.
    """
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


def delete_audience_room(room_id: str, enterprise_name: Optional[str] = None) -> bool:
    """Delete an AudienceRoom (profiles should cascade delete).
    
    Args:
        room_id: The audience room ID
        enterprise_name: Optional enterprise name (gamma, app, entelligence, beta). Defaults to AUDIENCE_DATABASE_URL if None.
    """
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor() as cur:
            cur.execute('DELETE FROM "AudienceRoom" WHERE id = %s', (room_id,))
            return cur.rowcount > 0


# ============================================
# AudienceProfile Operations (Audience Database)
# ============================================

def find_audience_profiles(
    audience_room_id: Optional[str] = None,
    all_profiles: bool = False,
    enterprise_name: Optional[str] = None
) -> List[AudienceProfile]:
    """Find AudienceProfiles, optionally filtered by room ID.
    
    Args:
        audience_room_id: Optional room ID to filter by
        all_profiles: If True, return all profiles (requires enterprise_name if not default)
        enterprise_name: Optional enterprise name (gamma, app, entelligence, beta). 
                        Defaults to AUDIENCE_DATABASE_URL if None.
    """
    with get_enterprise_audience_connection(enterprise_name) as conn:
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
    """Find an AudienceProfile by ID, optionally including the room.
    
    Args:
        profile_id: The profile ID
        include_room: Whether to include the associated room
        enterprise_name: Optional enterprise name (gamma, app, entelligence, beta). Defaults to AUDIENCE_DATABASE_URL if None.
    """
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


def update_audience_profile(profile_id: str, data: Dict[str, Any], enterprise_name: Optional[str] = None) -> Optional[AudienceProfile]:
    """Update an AudienceProfile record.
    
    Args:
        profile_id: The profile ID to update
        data: Dictionary of fields to update
        enterprise_name: Optional enterprise name (gamma, app, entelligence, beta). 
                        Defaults to AUDIENCE_DATABASE_URL if None.
    """
    if not data:
        return find_audience_profile_by_id(profile_id, enterprise_name=enterprise_name)
    
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
    
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            query = f'UPDATE "AudienceProfile" SET {", ".join(set_clauses)} WHERE id = %s RETURNING *'
            cur.execute(query, values)
            row = cur.fetchone()
            return AudienceProfile(row) if row else None


def delete_audience_profiles_by_room(room_id: str, enterprise_name: Optional[str] = None) -> int:
    """Delete all profiles in an audience room.
    
    Args:
        room_id: The audience room ID
        enterprise_name: Optional enterprise name (gamma, app, entelligence, beta). Defaults to AUDIENCE_DATABASE_URL if None.
    """
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor() as cur:
            cur.execute(
                'DELETE FROM "AudienceProfile" WHERE "audienceRoomId" = %s',
                (room_id,)
            )
            return cur.rowcount


# ============================================
# PostClassifier Operations (Audience Database)
# ============================================

def find_post_classifier_by_id(classifier_id: str) -> Optional[PostClassifier]:
    """Find a PostClassifier by ID."""
    with get_audience_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                '''
                SELECT id, "userId", name, description, prompt, labels, examples, "createdAt", "updatedAt"
                FROM "PostClassifier" 
                WHERE id = %s
                ''',
                (classifier_id,)
            )
            row = cur.fetchone()
            return PostClassifier(row) if row else None


def query_first(sql: str, *args) -> Optional[Dict[str, Any]]:
    """Execute a raw SQL query and return the first result."""
    with get_audience_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, args)
            return cur.fetchone()


# ============================================
# Preview Operations (Audience Database)
# ============================================

def find_all_previews(user_id: Optional[str] = None, enterprise_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Fetch all preview records from the database, optionally filtered by user_id.
    
    Args:
        user_id: Optional user ID to filter by
        enterprise_name: Optional enterprise name (gamma, app, entelligence, beta). 
                        Defaults to AUDIENCE_DATABASE_URL if None.
    """
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if user_id:
                cur.execute('SELECT * FROM "previews" WHERE user_id = %s ORDER BY created_at DESC', (user_id,))
            else:
                cur.execute('SELECT * FROM "previews" ORDER BY created_at DESC')
            rows = cur.fetchall()
            return [dict(row) for row in rows]


def find_preview_by_room_id(room_id: str, user_id: Optional[str] = None, enterprise_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Fetch a single preview by room ID and optionally user ID.
    
    Args:
        room_id: The room ID to fetch preview for
        user_id: Optional user ID to filter by
        enterprise_name: Optional enterprise name (gamma, app, entelligence, beta). 
                        Defaults to AUDIENCE_DATABASE_URL if None.
    """
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if user_id:
                cur.execute('SELECT * FROM "previews" WHERE room_id = %s AND user_id = %s', (room_id, user_id))
            else:
                # If no user_id provided, get the first match (for backward compatibility)
                cur.execute('SELECT * FROM "previews" WHERE room_id = %s LIMIT 1', (room_id,))
            row = cur.fetchone()
            return dict(row) if row else None


def ensure_preview_table_exists(enterprise_name: Optional[str] = None) -> bool:
    """
    Ensure the previews table exists with the improved schema.
    Creates or alters the table as needed.
    
    Args:
        enterprise_name: Optional enterprise name (gamma, app, entelligence, beta). 
                        Defaults to AUDIENCE_DATABASE_URL if None.
    
    WARNING: This only touches the previews table, no other tables are modified.
    """
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor() as cur:
            # Create or update the previews table with improved schema
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
            
            # Add missing columns if table already exists (for migration)
            # Check and add source column
            cur.execute("""
                DO $$ 
                BEGIN 
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                   WHERE table_name='previews' AND column_name='source') THEN
                        ALTER TABLE "previews" ADD COLUMN source VARCHAR(50);
                    END IF;
                END $$;
            """)
            
            # Check and add total_profile_count column
            cur.execute("""
                DO $$ 
                BEGIN 
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                   WHERE table_name='previews' AND column_name='total_profile_count') THEN
                        ALTER TABLE "previews" ADD COLUMN total_profile_count INTEGER DEFAULT 0;
                    END IF;
                END $$;
            """)
            
            # Remove traits column if it exists (cleanup)
            cur.execute("""
                DO $$ 
                BEGIN 
                    IF EXISTS (SELECT 1 FROM information_schema.columns 
                               WHERE table_name='previews' AND column_name='traits') THEN
                        ALTER TABLE "previews" DROP COLUMN traits;
                    END IF;
                END $$;
            """)
            
            logger.info("Preview table schema ensured successfully")
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
    """
    Insert or update a preview record.
    
    Args:
        enterprise_name: Optional enterprise name (gamma, app, entelligence, beta). 
                        Defaults to AUDIENCE_DATABASE_URL if None.
    
    WARNING: This only modifies the previews table, no other tables are touched.
    """
    now = datetime.utcnow()
    
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO "previews" 
                (room_id, user_id, name, description_summary, source, total_profile_count, profiles, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (room_id, user_id) 
                DO UPDATE SET
                    name = EXCLUDED.name,
                    description_summary = EXCLUDED.description_summary,
                    source = EXCLUDED.source,
                    total_profile_count = EXCLUDED.total_profile_count,
                    profiles = EXCLUDED.profiles,
                    updated_at = EXCLUDED.updated_at
                RETURNING *
            """, (
                room_id,
                user_id,
                name,
                description_summary,
                source,
                total_profile_count,
                Json(profiles) if profiles else None,
                now,
                now
            ))
            row = cur.fetchone()
            logger.info(f"Upserted preview for room {room_id}")
            return dict(row) if row else {}


def delete_preview(room_id: str, user_id: Optional[str] = None, enterprise_name: Optional[str] = None) -> bool:
    """
    Delete a preview record.
    
    Args:
        room_id: The room ID to delete preview for
        user_id: Optional user ID to scope deletion
        enterprise_name: Optional enterprise name (gamma, app, entelligence, beta). 
                        Defaults to AUDIENCE_DATABASE_URL if None.
    
    WARNING: This only deletes from previews table, no other tables are touched.
    """
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor() as cur:
            if user_id:
                cur.execute('DELETE FROM "previews" WHERE room_id = %s AND user_id = %s', (room_id, user_id))
            else:
                cur.execute('DELETE FROM "previews" WHERE room_id = %s', (room_id,))
            deleted = cur.rowcount > 0
            if deleted:
                logger.info(f"Deleted preview for room {room_id}")
            return deleted


def update_preview_name(
    room_id: str,
    new_name: str,
    enterprise_name: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Update the name of an audience room in the previews table.
    
    Args:
        room_id: The audience room ID to update
        new_name: The new name to set
        enterprise_name: Optional enterprise name (gamma, app, entelligence, beta). 
                        Defaults to AUDIENCE_DATABASE_URL if None.
    
    Returns:
        Updated preview record as a dictionary, or None if not found.
    
    WARNING: This only modifies the previews table, no other tables are touched.
    """
    now = datetime.utcnow()
    
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                UPDATE "previews"
                SET name = %s, updated_at = %s
                WHERE room_id = %s
                RETURNING *
            """, (new_name, now, room_id))
            row = cur.fetchone()
            if row:
                logger.info(f"Updated preview name for room {room_id} to '{new_name}'")
                return dict(row)
            else:
                logger.warning(f"Preview not found for room {room_id}")
                return None


def delete_orphaned_previews(enterprise_name: Optional[str] = None) -> Dict[str, int]:
    """
    Delete preview entries for rooms that no longer exist in the AudienceRoom table,
    and also delete duplicate preview entries (keeping only the most recent one per room_id).
    
    Args:
        enterprise_name: Optional enterprise name (gamma, app, entelligence, beta). 
                        Defaults to AUDIENCE_DATABASE_URL if None.
    
    Returns:
        Dictionary with counts of orphaned and duplicate entries deleted.
        Format: {"orphaned": count, "duplicates": count}
    
    WARNING: This only deletes from previews table, no other tables are touched.
    """
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor() as cur:
            # Step 1: Delete orphaned previews (previews for rooms that don't exist)
            cur.execute("""
                DELETE FROM "previews" p
                WHERE NOT EXISTS (
                    SELECT 1 FROM "AudienceRoom" ar 
                    WHERE ar.id = p.room_id
                )
            """)
            orphaned_count = cur.rowcount
            
            # Step 2: Delete duplicate previews (keep only the most recent one per room_id)
            # For each room_id, keep only the preview with the latest updated_at
            # If there are multiple previews with the same room_id, delete all except the most recent one
            cur.execute("""
                DELETE FROM "previews" p1
                WHERE EXISTS (
                    SELECT 1 FROM "previews" p2
                    WHERE p2.room_id = p1.room_id
                    AND (
                        p2.updated_at > p1.updated_at
                        OR (p2.updated_at = p1.updated_at AND p2.created_at > p1.created_at)
                        OR (p2.updated_at = p1.updated_at AND p2.created_at = p1.created_at AND p2.user_id > p1.user_id)
                    )
                )
            """)
            duplicates_count = cur.rowcount
            
            total_deleted = orphaned_count + duplicates_count
            
            if total_deleted > 0:
                logger.info(f"Cleanup complete: Deleted {orphaned_count} orphaned and {duplicates_count} duplicate preview entries")
            else:
                logger.info("No orphaned or duplicate preview entries found to delete")
            
            return {
                "orphaned": orphaned_count,
                "duplicates": duplicates_count,
                "total": total_deleted
            }


def find_all_audience_rooms_with_profiles(limit: int = 5, enterprise_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Fetch all audience rooms with their first N profiles.
    Used for bulk preview population.
    
    Args:
        limit: Number of profiles to fetch per room
        enterprise_name: Optional enterprise name (gamma, app, entelligence, beta). 
                        Defaults to AUDIENCE_DATABASE_URL if None.
    
    WARNING: This is READ-ONLY, no modifications to any tables.
    """
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Fetch all rooms
            cur.execute("""
                SELECT id, name, "descriptionS3Url", source, "userId", query, "createdAt"
                FROM "AudienceRoom"
                ORDER BY "createdAt" DESC
            """)
            rooms = [dict(row) for row in cur.fetchall()]
            
            # For each room, fetch profiles
            for room in rooms:
                # Get total profile count
                cur.execute(
                    'SELECT COUNT(*) as count FROM "AudienceProfile" WHERE "audienceRoomId" = %s',
                    (room['id'],)
                )
                count_row = cur.fetchone()
                room['total_profile_count'] = count_row['count'] if count_row else 0
                
                # Get first N profiles
                cur.execute("""
                    SELECT id, "profileName", "profileUrl", "profileDescriptionS3Url", source
                    FROM "AudienceProfile"
                    WHERE "audienceRoomId" = %s
                    ORDER BY "createdAt"
                    LIMIT %s
                """, (room['id'], limit))
                room['profiles'] = [dict(row) for row in cur.fetchall()]
            
            return rooms


def find_audience_room_with_profiles_for_preview(room_id: str, profile_limit: int = 5) -> Optional[Dict[str, Any]]:
    """
    Fetch a single audience room with its first N profiles for preview generation.
    
    WARNING: This is READ-ONLY, no modifications to any tables.
    """
    with get_audience_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Fetch room
            cur.execute("""
                SELECT id, name, "descriptionS3Url", source, "userId", query, "createdAt"
                FROM "AudienceRoom"
                WHERE id = %s
            """, (room_id,))
            room_row = cur.fetchone()
            
            if not room_row:
                return None
            
            room = dict(room_row)
            
            # Get total profile count
            cur.execute(
                'SELECT COUNT(*) as count FROM "AudienceProfile" WHERE "audienceRoomId" = %s',
                (room_id,)
            )
            count_row = cur.fetchone()
            room['total_profile_count'] = count_row['count'] if count_row else 0
            
            # Get first N profiles
            cur.execute("""
                SELECT id, "profileName", "profileUrl", "profileDescriptionS3Url", source
                FROM "AudienceProfile"
                WHERE "audienceRoomId" = %s
                ORDER BY "createdAt"
                LIMIT %s
            """, (room_id, profile_limit))
            room['profiles'] = [dict(row) for row in cur.fetchall()]
            
            return room


# ============================================
# Database Health Check
# ============================================

def check_main_db_connection() -> bool:
    """Check if main database connection is available."""
    try:
        with get_main_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                return True
    except Exception as e:
        logger.error(f"Main database connection check failed: {e}")
        return False


def check_audience_db_connection() -> bool:
    """Check if audience database connection is available."""
    try:
        with get_audience_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                return True
    except Exception as e:
        logger.error(f"Audience database connection check failed: {e}")
        return False


def is_main_db_available() -> bool:
    """Check if main database pool is available."""
    return get_main_pool() is not None


def is_audience_db_available() -> bool:
    """Check if audience database pool is available."""
    return get_audience_pool() is not None

