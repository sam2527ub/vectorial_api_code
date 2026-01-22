"""
Migration script for gamma database - ONLY migrates files for rooms in gamma database.
This script:
1. Gets all rooms from gamma database (ONLY)
2. For each room, extracts S3 keys from database URLs
3. Copies only those specific files to new location
4. Updates database URLs to point to new location
5. Deletes S3 folders in gamma/linkedin-audience/ and gamma/reddit-audience/ that don't exist in gamma database
"""

import os
import sys
import logging
from typing import Optional, List, Dict, Any, Set
from urllib.parse import urlparse
import boto3
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import database
from app.config import s3_bucket, s3_region
from app.utils.s3_utils import get_audience_type_from_source, extract_s3_key_from_url

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

s3_client = boto3.client('s3', region_name=s3_region)
ENTERPRISE_NAME = "gamma"


def copy_single_file(old_key: str, new_key: str) -> bool:
    """Copy a single S3 file if destination doesn't exist."""
    try:
        # Check if destination exists
        try:
            s3_client.head_object(Bucket=s3_bucket, Key=new_key)
            logger.debug(f"Already exists: {new_key}")
            return True
        except s3_client.exceptions.ClientError as e:
            if e.response['Error']['Code'] != '404':
                raise
        
        # Check if source exists
        try:
            s3_client.head_object(Bucket=s3_bucket, Key=old_key)
        except s3_client.exceptions.ClientError as e:
            if e.response['Error']['Code'] == '404':
                logger.warning(f"Source not found: {old_key}")
                return False
            raise
        
        # Copy
        s3_client.copy_object(
            CopySource={'Bucket': s3_bucket, 'Key': old_key},
            Bucket=s3_bucket,
            Key=new_key
        )
        logger.info(f"Copied: {old_key} -> {new_key}")
        return True
    except Exception as e:
        logger.error(f"Error copying {old_key}: {e}")
        return False


def get_new_key_from_old_key(old_key: str, room_id: str, source: Optional[str]) -> Optional[str]:
    """Convert old S3 key to new S3 key."""
    # Extract relative path
    relative_path = None
    
    if old_key.startswith(f"audiences/{room_id}/"):
        relative_path = old_key[len(f"audiences/{room_id}/"):]
    elif old_key.startswith(f"linkedin-audience/{ENTERPRISE_NAME}/{room_id}/"):
        relative_path = old_key[len(f"linkedin-audience/{ENTERPRISE_NAME}/{room_id}/"):]
    elif old_key.startswith(f"reddit-audience/{room_id}/"):
        relative_path = old_key[len(f"reddit-audience/{room_id}/"):]
    else:
        # Try to find room_id in path
        parts = old_key.split('/')
        for i, part in enumerate(parts):
            if part == room_id and i < len(parts) - 1:
                relative_path = '/'.join(parts[i+1:])
                break
    
    if not relative_path:
        return None
    
    # Determine audience type
    audience_type = get_audience_type_from_source(source)
    
    # Build new key
    return f"{ENTERPRISE_NAME}/{audience_type}/{room_id}/{relative_path}"


def migrate_room_files_from_urls(room: Any) -> Dict[str, Any]:
    """Migrate files for a room based on URLs in database."""
    room_id = room.id
    room_source = room.source
    new_prefix = f"{ENTERPRISE_NAME}/{get_audience_type_from_source(room_source)}/{room_id}/"
    
    files_copied = 0
    files_to_copy = []
    
    # Collect all S3 keys from room URLs
    if room.descriptionS3Url:
        old_key = extract_s3_key_from_url(room.descriptionS3Url)
        if old_key and new_prefix not in room.descriptionS3Url:
            files_to_copy.append(('description', old_key))
    
    if room.indexesS3Url:
        old_key = extract_s3_key_from_url(room.indexesS3Url)
        if old_key and new_prefix not in room.indexesS3Url:
            files_to_copy.append(('indexes', old_key))
    
    # Collect all S3 keys from profile URLs
    profile_files = []
    if hasattr(room, 'profiles') and room.profiles:
        for profile in room.profiles:
            for field in ['profileDescriptionS3Url', 'postsS3Url', 'commentsS3Url']:
                url = getattr(profile, field, None)
                if url and new_prefix not in url:
                    old_key = extract_s3_key_from_url(url)
                    if old_key:
                        profile_files.append((profile.id, field, old_key))
    
    # Copy room files
    for file_type, old_key in files_to_copy:
        new_key = get_new_key_from_old_key(old_key, room_id, room_source)
        if new_key and copy_single_file(old_key, new_key):
            files_copied += 1
    
    # Copy profile files
    for profile_id, field, old_key in profile_files:
        new_key = get_new_key_from_old_key(old_key, room_id, room_source)
        if new_key and copy_single_file(old_key, new_key):
            files_copied += 1
    
    return {
        'files_copied': files_copied,
        'files_to_copy': len(files_to_copy) + len(profile_files)
    }


def get_all_room_ids_from_database() -> Set[str]:
    """Get all room IDs from gamma database."""
    try:
        from app.database.connection import get_enterprise_audience_connection
        from psycopg2.extras import RealDictCursor
        
        room_ids = set()
        with get_enterprise_audience_connection(ENTERPRISE_NAME) as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute('SELECT id FROM "AudienceRoom"')
                rows = cur.fetchall()
                for row in rows:
                    room_ids.add(row['id'])
        
        logger.info(f"Found {len(room_ids)} room IDs in gamma database")
        return room_ids
    except Exception as e:
        logger.error(f"Error getting room IDs from database: {e}")
        return set()


def delete_orphaned_folders(valid_room_ids: Set[str]):
    """Delete S3 folders in gamma/linkedin-audience/ and gamma/reddit-audience/ that don't exist in database."""
    logger.info("\n" + "="*60)
    logger.info("Cleaning up orphaned folders...")
    logger.info("="*60)
    
    folders_to_check = [
        f"{ENTERPRISE_NAME}/linkedin-audience/",
        f"{ENTERPRISE_NAME}/reddit-audience/",
    ]
    
    total_deleted = 0
    
    for folder_prefix in folders_to_check:
        logger.info(f"\nChecking folder: {folder_prefix}")
        
        try:
            paginator = s3_client.get_paginator('list_objects_v2')
            pages = paginator.paginate(Bucket=s3_bucket, Prefix=folder_prefix, Delimiter='/')
            
            for page in pages:
                # Get "folders" (common prefixes)
                if 'CommonPrefixes' in page:
                    for prefix_info in page['CommonPrefixes']:
                        folder_path = prefix_info['Prefix']
                        # Extract room_id from folder path: gamma/linkedin-audience/{room_id}/
                        parts = folder_path.rstrip('/').split('/')
                        if len(parts) >= 3:
                            room_id = parts[2]
                            
                            # Check if room exists in database
                            if room_id not in valid_room_ids:
                                logger.info(f"Deleting orphaned folder: {folder_path} (room_id not in database)")
                                
                                # Delete all objects in this folder
                                try:
                                    objects_to_delete = []
                                    for obj_page in paginator.paginate(Bucket=s3_bucket, Prefix=folder_path):
                                        if 'Contents' in obj_page:
                                            for obj in obj_page['Contents']:
                                                objects_to_delete.append({'Key': obj['Key']})
                                    
                                    if objects_to_delete:
                                        # Delete in batches of 1000
                                        for i in range(0, len(objects_to_delete), 1000):
                                            batch = objects_to_delete[i:i+1000]
                                            s3_client.delete_objects(
                                                Bucket=s3_bucket,
                                                Delete={'Objects': batch, 'Quiet': True}
                                            )
                                        logger.info(f"  Deleted {len(objects_to_delete)} objects from {folder_path}")
                                        total_deleted += len(objects_to_delete)
                                except Exception as e:
                                    logger.error(f"Error deleting folder {folder_path}: {e}")
        
        except Exception as e:
            logger.error(f"Error checking folder {folder_prefix}: {e}")
    
    logger.info(f"\nTotal objects deleted from orphaned folders: {total_deleted}")
    return total_deleted


def main():
    logger.info("="*60)
    logger.info("Gamma S3 Migration - Database-Driven")
    logger.info(f"Enterprise: {ENTERPRISE_NAME}")
    logger.info("="*60)
    logger.info("ONLY migrating files for rooms in gamma database")
    logger.info("="*60)
    
    # Get all room IDs from database first
    valid_room_ids = get_all_room_ids_from_database()
    
    if not valid_room_ids:
        logger.error("No rooms found in gamma database!")
        return
    
    # Get all rooms from gamma database
    try:
        from app.database.connection import get_enterprise_audience_connection
        from psycopg2.extras import RealDictCursor
        
        rooms = []
        with get_enterprise_audience_connection(ENTERPRISE_NAME) as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute('SELECT * FROM "AudienceRoom" ORDER BY "createdAt"')
                room_rows = cur.fetchall()
                
                for row in room_rows:
                    room = database.AudienceRoom(row)
                    room = database.find_audience_room_by_id(room.id, include_profiles=True, enterprise_name=ENTERPRISE_NAME)
                    if room:
                        rooms.append(room)
        
        logger.info(f"Found {len(rooms)} rooms in gamma database")
        
        # Migrate each room
        total_files_copied = 0
        total_rooms_updated = 0
        total_profiles_updated = 0
        
        for idx, room in enumerate(rooms, 1):
            logger.info(f"\n[{idx}/{len(rooms)}] Processing room: {room.id} ({room.name})")
            
            # Migrate files
            result = migrate_room_files_from_urls(room)
            total_files_copied += result['files_copied']
            
            # Update database URLs
            room_source = room.source
            new_prefix = f"{ENTERPRISE_NAME}/{get_audience_type_from_source(room_source)}/{room.id}/"
            
            room_updates = {}
            if room.descriptionS3Url and new_prefix not in room.descriptionS3Url:
                old_key = extract_s3_key_from_url(room.descriptionS3Url)
                if old_key:
                    new_key = get_new_key_from_old_key(old_key, room.id, room_source)
                    if new_key:
                        room_updates['descriptionS3Url'] = f"https://{s3_bucket}.s3.{s3_region}.amazonaws.com/{new_key}"
            
            if room.indexesS3Url and new_prefix not in room.indexesS3Url:
                old_key = extract_s3_key_from_url(room.indexesS3Url)
                if old_key:
                    new_key = get_new_key_from_old_key(old_key, room.id, room_source)
                    if new_key:
                        room_updates['indexesS3Url'] = f"https://{s3_bucket}.s3.{s3_region}.amazonaws.com/{new_key}"
            
            if room_updates:
                database.update_audience_room(room.id, room_updates, enterprise_name=ENTERPRISE_NAME)
                total_rooms_updated += 1
                logger.info(f"  Updated room URLs")
            
            # Update profile URLs
            if hasattr(room, 'profiles') and room.profiles:
                profile_count = 0
                for profile in room.profiles:
                    profile_updates = {}
                    
                    for field in ['profileDescriptionS3Url', 'postsS3Url', 'commentsS3Url']:
                        url = getattr(profile, field, None)
                        if url and new_prefix not in url:
                            old_key = extract_s3_key_from_url(url)
                            if old_key:
                                new_key = get_new_key_from_old_key(old_key, room.id, room_source)
                                if new_key:
                                    profile_updates[field] = f"https://{s3_bucket}.s3.{s3_region}.amazonaws.com/{new_key}"
                    
                    if profile_updates:
                        database.update_audience_profile(profile.id, profile_updates, enterprise_name=ENTERPRISE_NAME)
                        profile_count += 1
                
                if profile_count > 0:
                    total_profiles_updated += profile_count
                    logger.info(f"  Updated {profile_count} profile URLs")
        
        logger.info("\n" + "="*60)
        logger.info("MIGRATION COMPLETE")
        logger.info("="*60)
        logger.info(f"Total files copied: {total_files_copied}")
        logger.info(f"Total rooms updated: {total_rooms_updated}")
        logger.info(f"Total profiles updated: {total_profiles_updated}")
        logger.info("="*60)
        
        # Clean up orphaned folders
        deleted_count = delete_orphaned_folders(valid_room_ids)
        
        logger.info("\n" + "="*60)
        logger.info("FINAL SUMMARY")
        logger.info("="*60)
        logger.info(f"Files copied: {total_files_copied}")
        logger.info(f"Rooms updated: {total_rooms_updated}")
        logger.info(f"Profiles updated: {total_profiles_updated}")
        logger.info(f"Orphaned objects deleted: {deleted_count}")
        logger.info("="*60)
        logger.info("✓ Migration and cleanup complete!")
        logger.info("✓ Only gamma/linkedin-audience/ and gamma/reddit-audience/ folders were cleaned")
        logger.info("✓ No other S3 files or database records were deleted")
        
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
