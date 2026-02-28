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
# Dynamic enterprise pools (replaces individual pool variables)
_enterprise_pools: Dict[str, Optional[ThreadedConnectionPool]] = {}


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


def get_enterprise_pool(enterprise_name: str) -> Optional[ThreadedConnectionPool]:
    """Dynamically get or create pool for an enterprise."""
    from app.database.enterprise_registry import get_enterprise_env_var, format_display_name
    
    normalized = enterprise_name.lower().strip()
    
    # Check if pool already exists (same pattern as get_audience_pool)
    if normalized in _enterprise_pools and _enterprise_pools[normalized] is not None:
        return _enterprise_pools[normalized]
    
    # Create pool if it doesn't exist
    env_var = get_enterprise_env_var(normalized)
    database_url = os.getenv(env_var)
    
    if database_url:
        try:
            pool = ThreadedConnectionPool(1, 10, database_url)
            display_name = format_display_name(normalized)
            logger.info(f"{display_name} database pool initialized successfully")
            _enterprise_pools[normalized] = pool
            return pool
        except Exception as e:
            logger.error(f"Failed to initialize {normalized} database pool: {e}")
    
    return None


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
        enterprise_name: Optional enterprise name. Auto-discovered from environment variables.
        If None or not provided, defaults to AUDIENCE_DATABASE_URL
    """
    from app.database.enterprise_registry import is_valid_enterprise, get_enterprise_env_var, format_display_name
    
    normalized_enterprise = enterprise_name.lower().strip() if enterprise_name else None
    logger.info(f"get_enterprise_audience_connection called with enterprise_name='{enterprise_name}', normalized='{normalized_enterprise}'")
    
    pool = None
    
    if normalized_enterprise and is_valid_enterprise(normalized_enterprise):
        pool = get_enterprise_pool(normalized_enterprise)
        if not pool:
            env_var = get_enterprise_env_var(normalized_enterprise)
            display_name = format_display_name(normalized_enterprise)
            logger.error(f"{display_name} database pool is None - {env_var} might not be set")
            raise Exception(f"{display_name} database pool not available. Please set {env_var}.")
        display_name = format_display_name(normalized_enterprise)
        logger.info(f"{display_name} database pool retrieved successfully")
    else:
        # Default to audience database
        logger.info(f"Using default AUDIENCE database connection (enterprise_name was '{enterprise_name}', normalized='{normalized_enterprise}')")
        pool = get_audience_pool()
        if not pool:
            logger.error("Audience database pool is None - AUDIENCE_DATABASE_URL may not be set")
            raise Exception("Audience database pool not available. Please set AUDIENCE_DATABASE_URL.")
        logger.info("Audience database pool retrieved successfully")
    
    logger.info(f"Getting connection from pool for enterprise='{normalized_enterprise}'")
    conn = pool.getconn()
    logger.info(f"Connection acquired from pool for enterprise='{normalized_enterprise}'")
    try:
        logger.debug(f"Yielding connection for enterprise='{normalized_enterprise}'")
        yield conn
        logger.debug(f"Committing transaction for enterprise='{normalized_enterprise}'")
        conn.commit()
        logger.info(f"Transaction committed successfully for enterprise='{normalized_enterprise}'")
    except Exception as e:
        logger.error(f"Exception occurred in transaction for enterprise='{normalized_enterprise}': {e}", exc_info=True)
        logger.info(f"Rolling back transaction for enterprise='{normalized_enterprise}'")
        conn.rollback()
        logger.info(f"Transaction rolled back for enterprise='{normalized_enterprise}'")
        raise e
    finally:
        logger.info(f"Returning connection to pool for enterprise='{normalized_enterprise}'")
        pool.putconn(conn)
        logger.info(f"Connection returned to pool for enterprise='{normalized_enterprise}'")

def close_pools():
    """Close all connection pools."""
    global _main_pool, _audience_pool, _enterprise_pools
    
    # Close main pool
    if _main_pool:
        _main_pool.closeall()
        _main_pool = None
        logger.info("Main database pool closed")
    
    # Close audience pool
    if _audience_pool:
        _audience_pool.closeall()
        _audience_pool = None
        logger.info("Audience database pool closed")
    
    # Close all enterprise pools dynamically
    from app.database.enterprise_registry import format_display_name
    
    for enterprise_name, pool in _enterprise_pools.items():
        if pool:
            pool.closeall()
            display_name = format_display_name(enterprise_name)
            logger.info(f"{display_name} database pool closed")
    _enterprise_pools.clear()


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


class ParallelSearchJob:
    """Represents a ParallelSearchJob record."""
    def __init__(self, row: Dict[str, Any]):
        self.id = row.get('id')
        self.status = row.get('status', 'PENDING')
        self.query = row.get('query')
        self.model = row.get('model', 'core')
        self.matchLimit = row.get('matchLimit') or row.get('matchlimit')
        self.parallelRunId = row.get('parallelRunId') or row.get('parallelrunid')
        self.profiles = row.get('profiles')
        self.result = row.get('result')
        self.error = row.get('error')
        self.enterpriseName = row.get('enterpriseName') or row.get('enterprisename')
        self.createdAt = row.get('createdAt') or row.get('createdat')
        self.updatedAt = row.get('updatedAt') or row.get('updatedat')


class CommentScrapeJob:
    """Represents a CommentScrapeJob record (comment scrape job metadata, replaces S3 job.json)."""
    def __init__(self, row: Dict[str, Any]):
        self.id = row.get('id')
        self.audienceRoomId = row.get('audienceRoomId') or row.get('audienceroomid')
        self.enterpriseName = row.get('enterpriseName') or row.get('enterprisename')
        self.status = row.get('status', 'PROCESSING')
        self.result = row.get('result')
        self.error = row.get('error')
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
# ============================================
# CommentScrapeJob Operations (Audience Database)
# ============================================

def ensure_comment_scrape_job_table_exists(enterprise_name: Optional[str] = None) -> None:
    """Create CommentScrapeJob table if it doesn't exist. Safe to call on every request."""
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS "CommentScrapeJob" (
                    "id" TEXT NOT NULL,
                    "audienceRoomId" TEXT NOT NULL,
                    "enterpriseName" TEXT,
                    "status" TEXT NOT NULL DEFAULT 'PROCESSING',
                    "result" JSONB,
                    "error" TEXT,
                    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    "updatedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT "CommentScrapeJob_pkey" PRIMARY KEY ("id")
                )
            """)
        with conn.cursor() as cur:
            cur.execute('CREATE INDEX IF NOT EXISTS "CommentScrapeJob_status_idx" ON "CommentScrapeJob"("status")')
        with conn.cursor() as cur:
            cur.execute('CREATE INDEX IF NOT EXISTS "CommentScrapeJob_audienceRoomId_idx" ON "CommentScrapeJob"("audienceRoomId")')
        with conn.cursor() as cur:
            cur.execute('CREATE INDEX IF NOT EXISTS "CommentScrapeJob_createdAt_idx" ON "CommentScrapeJob"("createdAt")')
    logger.info("CommentScrapeJob table ensured to exist")


def create_comment_scrape_job(
    audience_room_id: str,
    run_ids: List[str],
    enterprise_name: Optional[str] = None,
) -> CommentScrapeJob:
    """Create a new CommentScrapeJob record. result JSON holds run_ids, batches_total, batches_completed, final_result."""
    ensure_comment_scrape_job_table_exists(enterprise_name)
    job_id = str(uuid.uuid4())
    now = datetime.utcnow()
    result = {
        "run_ids": run_ids,
        "batches_total": len(run_ids),
        "batches_completed": 0,
        "final_result": None,
    }
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO "CommentScrapeJob" (id, "audienceRoomId", "enterpriseName", status, result, "createdAt", "updatedAt")
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (job_id, audience_room_id, enterprise_name, 'PROCESSING', Json(result), now, now)
            )
            row = cur.fetchone()
            return CommentScrapeJob(row)


def find_comment_scrape_job_by_id(job_id: str, enterprise_name: Optional[str] = None) -> Optional[CommentScrapeJob]:
    """Find a CommentScrapeJob by ID."""
    ensure_comment_scrape_job_table_exists(enterprise_name)
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT * FROM "CommentScrapeJob" WHERE id = %s', (job_id,))
            row = cur.fetchone()
            return CommentScrapeJob(row) if row else None


def find_latest_comment_scrape_job_by_audience_room_id(
    audience_room_id: str,
    enterprise_name: Optional[str] = None,
) -> Optional[CommentScrapeJob]:
    """Find the most recent CommentScrapeJob for an audience room (for polling by audience_room_id)."""
    ensure_comment_scrape_job_table_exists(enterprise_name)
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                'SELECT * FROM "CommentScrapeJob" WHERE "audienceRoomId" = %s ORDER BY "createdAt" DESC LIMIT 1',
                (audience_room_id,)
            )
            row = cur.fetchone()
            return CommentScrapeJob(row) if row else None


def update_comment_scrape_job(
    job_id: str,
    data: Dict[str, Any],
    enterprise_name: Optional[str] = None,
) -> Optional[CommentScrapeJob]:
    """Update a CommentScrapeJob record. data can include status, result, error."""
    ensure_comment_scrape_job_table_exists(enterprise_name)
    if not data:
        return find_comment_scrape_job_by_id(job_id, enterprise_name=enterprise_name)
    set_clauses = []
    values = []
    field_mapping = {
        'status': '"status"',
        'result': '"result"',
        'error': '"error"',
    }
    for key, value in data.items():
        if key in field_mapping:
            set_clauses.append(f'{field_mapping[key]} = %s')
            if key == 'result':
                values.append(Json(value) if value is not None else None)
            else:
                values.append(value)
    set_clauses.append('"updatedAt" = %s')
    values.append(datetime.utcnow())
    values.append(job_id)
    try:
        with get_enterprise_audience_connection(enterprise_name) as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    f'UPDATE "CommentScrapeJob" SET {", ".join(set_clauses)} WHERE id = %s RETURNING *',
                    values
                )
                row = cur.fetchone()
                return CommentScrapeJob(row) if row else None
    except Exception as e:
        logger.error(f"Exception in update_comment_scrape_job for job {job_id}: {e}", exc_info=True)
        raise


# ============================================
# ParallelSearchJob Operations (Audience Database)
# ============================================

def create_parallel_search_job(
    query: str,
    model: str = 'core',
    match_limit: int = 100,
    enterprise_name: Optional[str] = None
) -> ParallelSearchJob:
    """Create a new ParallelSearchJob record.
    
    Args:
        query: Search query string
        model: Model to use ('core' or 'base')
        match_limit: Maximum number of matches
        enterprise_name: Optional enterprise name (gamma, app, entelligence, beta). 
                        Defaults to AUDIENCE_DATABASE_URL if None.
    """
    job_id = str(uuid.uuid4())
    now = datetime.utcnow()
    
    # Ensure the ParallelSearchJob table exists
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor() as cur:
            # Create table if it doesn't exist
            cur.execute("""
                CREATE TABLE IF NOT EXISTS "ParallelSearchJob" (
                    id VARCHAR(255) PRIMARY KEY,
                    status VARCHAR(50) DEFAULT 'PENDING',
                    query TEXT NOT NULL,
                    model VARCHAR(50) DEFAULT 'core',
                    "matchLimit" INTEGER DEFAULT 100,
                    "parallelRunId" VARCHAR(255),
                    profiles JSONB,
                    result JSONB,
                    error TEXT,
                    "enterpriseName" VARCHAR(50),
                    "createdAt" TIMESTAMP DEFAULT NOW(),
                    "updatedAt" TIMESTAMP DEFAULT NOW()
                )
            """)
            
            # Create indexes
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_parallel_search_job_status 
                ON "ParallelSearchJob" (status)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_parallel_search_job_created 
                ON "ParallelSearchJob" ("createdAt")
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_parallel_search_job_enterprise 
                ON "ParallelSearchJob" ("enterpriseName")
            """)
    
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO "ParallelSearchJob" 
                (id, status, query, model, "matchLimit", "enterpriseName", "createdAt", "updatedAt")
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (job_id, 'PENDING', query, model, match_limit, enterprise_name, now, now)
            )
            row = cur.fetchone()
            return ParallelSearchJob(row)


def find_parallel_search_job_by_id(job_id: str, enterprise_name: Optional[str] = None) -> Optional[ParallelSearchJob]:
    """Find a ParallelSearchJob by ID.
    
    Args:
        job_id: The parallel search job ID
        enterprise_name: Optional enterprise name (gamma, app, entelligence, beta). 
                        Defaults to AUDIENCE_DATABASE_URL if None.
    """
    logger.info(f"find_parallel_search_job_by_id called with job_id={job_id}, enterprise_name={enterprise_name}")
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT * FROM "ParallelSearchJob" WHERE id = %s', (job_id,))
            row = cur.fetchone()
            if row:
                logger.info(f"Found ParallelSearchJob {job_id} in database (enterprise_name={enterprise_name})")
            else:
                logger.warning(f"ParallelSearchJob {job_id} not found in database (enterprise_name={enterprise_name})")
            return ParallelSearchJob(row) if row else None


def update_parallel_search_job(job_id: str, data: Dict[str, Any], enterprise_name: Optional[str] = None) -> Optional[ParallelSearchJob]:
    """Update a ParallelSearchJob record.
    
    Args:
        job_id: The parallel search job ID to update
        data: Dictionary of fields to update
        enterprise_name: Optional enterprise name (gamma, app, entelligence, beta). 
                        Defaults to AUDIENCE_DATABASE_URL if None.
    """
    logger.info(f"update_parallel_search_job called: job_id={job_id}, enterprise_name={enterprise_name}, data_keys={list(data.keys())}")
    
    if not data:
        return find_parallel_search_job_by_id(job_id, enterprise_name=enterprise_name)
    
    # Build update query dynamically
    set_clauses = []
    values = []
    
    field_mapping = {
        'status': '"status"',
        'parallelRunId': '"parallelRunId"',
        'profiles': '"profiles"',
        'result': '"result"',
        'error': '"error"',
    }
    
    for key, value in data.items():
        if key in field_mapping:
            set_clauses.append(f'{field_mapping[key]} = %s')
            # Handle JSON fields
            if key in ['profiles', 'result']:
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
                query = f'UPDATE "ParallelSearchJob" SET {", ".join(set_clauses)} WHERE id = %s RETURNING *'
                logger.info(f"Executing update query for job {job_id} (enterprise_name={enterprise_name})")
                cur.execute(query, values)
                row = cur.fetchone()
                if row:
                    logger.info(f"Successfully updated job {job_id} in database (enterprise_name={enterprise_name})")
                else:
                    logger.warning(f"UPDATE query returned no rows for job {job_id} - job may not exist in database (enterprise_name={enterprise_name})")
                return ParallelSearchJob(row) if row else None
    except Exception as e:
        logger.error(f"Exception in update_parallel_search_job for job {job_id} (enterprise_name={enterprise_name}): {e}", exc_info=True)
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
    logger.info(f"create_audience_room called: room_id={room_id}, enterprise_name={enterprise_name}, name={name}, user_id={user_id}")
    logger.info(f"create_audience_room params: source={source}, query={query}, indexes_s3_url={indexes_s3_url}")
    logger.info(f"create_audience_room profiles_data count: {len(profiles_data) if profiles_data else 0}")
    
    now = datetime.utcnow()
    logger.info(f"Opening database connection for enterprise='{enterprise_name}'")
    
    try:
        with get_enterprise_audience_connection(enterprise_name) as conn:
            logger.info(f"Database connection opened successfully for enterprise='{enterprise_name}'")
            
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                logger.info(f"Cursor created for enterprise='{enterprise_name}'")
                
                # Create the room
                logger.info(f"Executing INSERT INTO AudienceRoom: room_id={room_id}, name={name}, enterprise={enterprise_name}")
                logger.info(f"INSERT values: description_s3_url={description_s3_url}, user_id={user_id}, source={source}, query={query}")
                
                try:
                    cur.execute(
                        """
                        INSERT INTO "AudienceRoom" (id, name, "descriptionS3Url", "userId", "source", "query", "indexesS3Url", "createdAt", "updatedAt")
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING *
                        """,
                        (room_id, name, description_s3_url, user_id, source, query, indexes_s3_url, now, now)
                    )
                    logger.info(f"INSERT INTO AudienceRoom executed successfully for room_id={room_id}")
                    
                    room_row = cur.fetchone()
                    if not room_row:
                        logger.error(f"INSERT INTO AudienceRoom FAILED: No row returned for room_id={room_id}, enterprise={enterprise_name}")
                        raise Exception(f"Failed to create AudienceRoom {room_id}: INSERT returned no row")
                    
                    logger.info(f"INSERT INTO AudienceRoom SUCCESS: Row fetched for room_id={room_id}, enterprise={enterprise_name}")
                    logger.info(f"Room row data: {dict(room_row)}")
                    
                    room = AudienceRoom(room_row)
                    logger.info(f"AudienceRoom object created: id={room.id}, name={room.name}, enterprise={enterprise_name}")
                    
                except Exception as insert_error:
                    logger.error(f"ERROR during INSERT INTO AudienceRoom for room_id={room_id}, enterprise={enterprise_name}: {insert_error}", exc_info=True)
                    raise
                
                # Create profiles if provided
                if profiles_data:
                    logger.info(f"Creating {len(profiles_data)} profiles for room_id={room_id}, enterprise={enterprise_name}")
                    for idx, profile_data in enumerate(profiles_data):
                        profile_id = profile_data.get('id', str(uuid.uuid4()))
                        logger.info(f"Creating profile {idx+1}/{len(profiles_data)}: profile_id={profile_id}, room_id={room_id}")
                        logger.info(f"Profile data: name={profile_data.get('profileName')}, url={profile_data.get('profileUrl')}")
                        
                        try:
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
                            logger.info(f"INSERT INTO AudienceProfile executed successfully for profile_id={profile_id}")
                            
                            profile_row = cur.fetchone()
                            if not profile_row:
                                logger.error(f"INSERT INTO AudienceProfile FAILED: No row returned for profile_id={profile_id}, room_id={room_id}")
                                raise Exception(f"Failed to create AudienceProfile {profile_id}: INSERT returned no row")
                            
                            logger.info(f"INSERT INTO AudienceProfile SUCCESS: Row fetched for profile_id={profile_id}")
                            room.profiles.append(AudienceProfile(profile_row))
                            logger.info(f"Profile {idx+1}/{len(profiles_data)} added to room object")
                            
                        except Exception as profile_error:
                            logger.error(f"ERROR during INSERT INTO AudienceProfile for profile_id={profile_id}, room_id={room_id}: {profile_error}", exc_info=True)
                            raise
                    
                    logger.info(f"Successfully created {len(profiles_data)} profiles for room_id={room_id}, enterprise={enterprise_name}")
                else:
                    logger.info(f"No profiles to create for room_id={room_id}, enterprise={enterprise_name}")
            
            logger.info(f"About to exit database connection context for enterprise='{enterprise_name}' (transaction will commit)")
        
        logger.info(f"Database connection context exited successfully for enterprise='{enterprise_name}'")
        logger.info(f"create_audience_room SUCCESS: Returning room object with id={room.id}, enterprise={enterprise_name}")
        return room
        
    except Exception as e:
        logger.error(f"FATAL ERROR in create_audience_room for room_id={room_id}, enterprise={enterprise_name}: {e}", exc_info=True)
        raise


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


def get_user_id_from_enterprise(
    enterprise_name: Optional[str] = None,
) -> Optional[str]:
    """
    Return the userId from any AudienceRoom in the given enterprise.
    """
    sql = """
        SELECT "userId"
        FROM "AudienceRoom"
        WHERE "userId" IS NOT NULL
        LIMIT 1
    """

    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql)
            row = cur.fetchone()

    if not row:
        return None

    # Defensive fallback in case of inconsistent key casing
    return row.get("userId") or row.get("userid")


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
    """
    Create or update an AudienceRoom.
    """
    now = datetime.utcnow()

    sql = """
        INSERT INTO "AudienceRoom" (
            id,
            name,
            "descriptionS3Url",
            "userId",
            "source",
            "query",
            "indexesS3Url",
            "createdAt",
            "updatedAt"
        )
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

    params = (
        room_id,
        name,
        description_s3_url,
        user_id,
        source,
        query,
        indexes_s3_url,
        now,
        now,
    )

    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            row = cur.fetchone()

    if not row:
        raise RuntimeError(f"AudienceRoom upsert failed (id={room_id})")

    return AudienceRoom(row)


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
    """
    Create or update an AudienceProfile.
    """
    now = datetime.utcnow()

    sql = """
        INSERT INTO "AudienceProfile" (
            id,
            "audienceRoomId",
            "profileName",
            "profileUrl",
            "profileDescriptionS3Url",
            "postsS3Url",
            "commentsS3Url",
            "source",
            "createdAt",
            "updatedAt"
        )
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

    params = (
        profile_id,
        audience_room_id,
        profile_name,
        profile_url,
        profile_description_s3_url,
        posts_s3_url,
        comments_s3_url,
        source,
        now,
        now,
    )

    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            row = cur.fetchone()

    if not row:
        raise RuntimeError(f"AudienceProfile upsert failed (id={profile_id})")

    return AudienceProfile(row)


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


def delete_audience_profile(profile_id: str, enterprise_name: Optional[str] = None) -> bool:
    """Delete a single audience profile by ID.
    
    Args:
        profile_id: The profile ID to delete
        enterprise_name: Optional enterprise name (gamma, app, entelligence, beta). Defaults to AUDIENCE_DATABASE_URL if None.
    
    Returns:
        True if a profile was deleted, False otherwise
    """
    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor() as cur:
            cur.execute(
                'DELETE FROM "AudienceProfile" WHERE id = %s',
                (profile_id,)
            )
            return cur.rowcount > 0


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

