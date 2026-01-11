#!/usr/bin/env python3
"""
Script to populate the Preview table with all audience rooms from GAMMA database.

This script:
1. Connects to the GAMMA database directly
2. Ensures the preview table exists with the improved schema
3. Fetches all audience rooms and their profiles
4. For each room, fetches data from S3 and creates preview records
5. Handles both LinkedIn and Reddit audience rooms automatically

Usage:
    python populate_all_previews.py

Environment variables:
    AWS_ACCESS_KEY_ID - AWS credentials for S3 access
    AWS_SECRET_ACCESS_KEY - AWS credentials for S3 access
    AWS_REGION - AWS region (default: us-west-2)
    AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME - S3 bucket name
"""

import os
import sys
import json
import boto3
from datetime import datetime
from typing import Optional, Dict, Any, List
from urllib.parse import urlparse

import psycopg2
from psycopg2.extras import RealDictCursor, Json
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# GAMMA database URL
DB_GAMMA_URL = "postgres://167d1648f8eaaa7fe51e5af9d382bd10e2743c2a60e5bb2c73b3aa02a5f85970:sk_2kLIhLxqqyVL3KKShSCHg@db.prisma.io:5432/?sslmode=require"

# S3 configuration
s3_bucket = os.getenv("AUDIENCE_BUCKET_NAME") or os.getenv("VECTOR_BUCKET_NAME")
s3_region = os.getenv("AWS_REGION", "us-west-2")

# Preview profile limit
PREVIEW_PROFILE_LIMIT = 5


def log(message: str, level: str = "INFO"):
    """Print a timestamped log message."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    prefix = {
        "INFO": "ℹ️ ",
        "SUCCESS": "✅",
        "WARNING": "⚠️ ",
        "ERROR": "❌",
        "DEBUG": "🔍",
    }.get(level, "  ")
    print(f"[{timestamp}] {prefix} {message}")


def get_connection(db_url: str, name: str):
    """Create a database connection."""
    log(f"Connecting to {name} database...", "DEBUG")
    try:
        conn = psycopg2.connect(db_url)
        log(f"Successfully connected to {name} database", "SUCCESS")
        return conn
    except Exception as e:
        log(f"Failed to connect to {name} database: {e}", "ERROR")
        sys.exit(1)


def extract_s3_key_from_url(s3_url: Optional[str]) -> Optional[str]:
    """Extract S3 key from a full S3 URL."""
    if not s3_url:
        return None
    try:
        parsed = urlparse(s3_url)
        key = parsed.path.lstrip('/')
        return key if key else None
    except Exception as e:
        log(f"Failed to extract S3 key from URL {s3_url}: {e}", "ERROR")
        return None


def fetch_json_from_s3_safe(s3_client, key: str) -> Dict[str, Any]:
    """Safely fetch JSON data from S3 by key. Returns empty dict on failure."""
    if not key:
        return {}
    try:
        response = s3_client.get_object(Bucket=s3_bucket, Key=key)
        content = response['Body'].read().decode('utf-8')
        return json.loads(content)
    except s3_client.exceptions.NoSuchKey:
        log(f"S3 object not found: {key}", "WARNING")
        return {}
    except Exception as e:
        log(f"Failed to fetch JSON from S3 for key {key}: {e}", "WARNING")
        return {}


def ensure_preview_table_exists(conn):
    """Ensure the previews table exists with the improved schema."""
    log("Ensuring preview table exists...", "INFO")
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
        
        # Add missing columns if table already exists
        for col_name, col_def in [
            ('source', 'VARCHAR(50)'),
            ('total_profile_count', 'INTEGER DEFAULT 0'),
        ]:
            cur.execute(f"""
                DO $$ 
                BEGIN 
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                   WHERE table_name='previews' AND column_name='{col_name}') THEN
                        ALTER TABLE "previews" ADD COLUMN {col_name} {col_def};
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
        
    conn.commit()
    log("Preview table schema ensured", "SUCCESS")


def fetch_all_rooms_with_profiles(conn) -> List[Dict[str, Any]]:
    """Fetch all audience rooms with their profiles."""
    log("Fetching all audience rooms...", "INFO")
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # Fetch all rooms
        cur.execute("""
            SELECT id, name, "descriptionS3Url", source, "userId", query, "createdAt"
            FROM "AudienceRoom"
            ORDER BY "createdAt" DESC
        """)
        rooms = [dict(row) for row in cur.fetchall()]
        
        # For each room, fetch profile count and first N profiles
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
            """, (room['id'], PREVIEW_PROFILE_LIMIT))
            room['profiles'] = [dict(row) for row in cur.fetchall()]
        
        log(f"Fetched {len(rooms)} audience rooms", "SUCCESS")
        return rooms


def build_profile_preview(profile: Dict[str, Any], source: str, s3_client) -> Dict[str, Any]:
    """Build a profile preview by fetching data from S3."""
    profile_key = extract_s3_key_from_url(profile.get('profileDescriptionS3Url'))
    profile_data = fetch_json_from_s3_safe(s3_client, profile_key) if profile_key else {}
    
    profile_source = profile.get('source') or source or ''
    is_linkedin = profile_source.lower() == 'linkedin'
    
    if is_linkedin:
        return {
            'id': profile.get('id'),
            'name': profile_data.get('name') or profile.get('profileName'),
            'linkedin_profile_url': profile_data.get('linkedin_profile_url') or profile.get('profileUrl'),
            'current_location': profile_data.get('current_location'),
            'current_company': profile_data.get('current_company'),
            'industry': profile_data.get('industry'),
            'summary': profile_data.get('summary'),
            'source': 'linkedin'
        }
    else:
        return {
            'id': profile.get('id'),
            'name': profile_data.get('username') or profile.get('profileName'),
            'reddit_profile_url': profile_data.get('userUrl') or profile.get('profileUrl'),
            'post_count': profile_data.get('postCount'),
            'comment_count': profile_data.get('commentCount'),
            'summary': profile_data.get('summary'),
            'source': 'reddit'
        }


def upsert_preview(conn, room_id: str, name: str, user_id: str, 
                   description_summary: Optional[str], source: Optional[str],
                   total_profile_count: int, profiles: Optional[List]) -> bool:
    """Insert or update a preview record."""
    now = datetime.utcnow()
    
    try:
        with conn.cursor() as cur:
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
        return True
    except Exception as e:
        log(f"Failed to upsert preview for room {room_id}: {e}", "ERROR")
        return False


def main():
    """Main function to populate all previews."""
    print("\n" + "=" * 70)
    print("📋 PREVIEW TABLE POPULATION SCRIPT")
    print("=" * 70)
    print(f"  Database: GAMMA")
    print(f"  S3 Bucket: {s3_bucket}")
    print(f"  Profiles per preview: {PREVIEW_PROFILE_LIMIT}")
    print("=" * 70 + "\n")
    
    # Check S3 configuration
    if not s3_bucket:
        log("S3 bucket not configured. Set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.", "ERROR")
        sys.exit(1)
    
    # Initialize S3 client
    try:
        s3_client = boto3.client("s3", region_name=s3_region)
        log(f"S3 client initialized for bucket {s3_bucket}", "SUCCESS")
    except Exception as e:
        log(f"Failed to initialize S3 client: {e}", "ERROR")
        sys.exit(1)
    
    # Connect to GAMMA database
    conn = get_connection(DB_GAMMA_URL, "GAMMA")
    
    try:
        # Ensure preview table exists
        ensure_preview_table_exists(conn)
        
        # Fetch all rooms with profiles
        rooms = fetch_all_rooms_with_profiles(conn)
        
        if not rooms:
            log("No audience rooms found", "WARNING")
            return
        
        # Process each room
        successful = 0
        failed = 0
        
        log(f"Processing {len(rooms)} room(s)...", "INFO")
        
        for i, room in enumerate(rooms, 1):
            room_id = room['id']
            room_name = room['name']
            source = room.get('source') or ''
            user_id = room.get('userId') or 'default'
            
            log(f"[{i}/{len(rooms)}] Processing: {room_name} ({source or 'unknown source'})", "INFO")
            
            try:
                # Fetch room description from S3
                description_key = extract_s3_key_from_url(room.get('descriptionS3Url'))
                room_description_data = fetch_json_from_s3_safe(s3_client, description_key) if description_key else {}
                
                description_summary = room_description_data.get('summary')
                
                # Build profile previews
                profile_previews = []
                for profile in room.get('profiles', []):
                    try:
                        preview = build_profile_preview(profile, source, s3_client)
                        profile_previews.append(preview)
                    except Exception as e:
                        log(f"  Failed to build preview for profile {profile.get('id')}: {e}", "WARNING")
                        profile_previews.append({
                            'id': profile.get('id'),
                            'name': profile.get('profileName'),
                            'summary': None,
                            'source': source.lower() if source else 'unknown'
                        })
                
                # Upsert preview
                success = upsert_preview(
                    conn=conn,
                    room_id=room_id,
                    name=room_name,
                    user_id=user_id,
                    description_summary=description_summary,
                    source=source.lower() if source else None,
                    total_profile_count=room.get('total_profile_count', 0),
                    profiles=profile_previews
                )
                
                if success:
                    log(f"  ✅ Preview created ({room.get('total_profile_count', 0)} profiles, {len(profile_previews)} in preview)", "SUCCESS")
                    successful += 1
                else:
                    failed += 1
                    
            except Exception as e:
                log(f"  ❌ Failed: {e}", "ERROR")
                failed += 1
        
        # Commit all changes
        conn.commit()
        log("All changes committed!", "SUCCESS")
        
        # Print summary
        print("\n" + "=" * 70)
        print("📊 SUMMARY")
        print("=" * 70)
        print(f"  Total rooms: {len(rooms)}")
        print(f"  Successful: {successful}")
        print(f"  Failed: {failed}")
        print("=" * 70 + "\n")
        
    except Exception as e:
        log(f"Error: {e}", "ERROR")
        conn.rollback()
        raise
    finally:
        conn.close()
        log("Database connection closed", "INFO")


if __name__ == "__main__":
    main()


