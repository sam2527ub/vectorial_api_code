"""Audience room endpoints."""
import uuid
import copy
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, HTTPException, Path, Query
from app.models.schemas import CreateAudienceRoomRequest, UpdateAudienceRoomNameRequest, CopyAudienceRoomToClientRequest
from app.config import s3_client, s3_bucket, logger, dynamodb_resource
from app.utils.helpers import ensure_db_available
from app.utils.s3_utils import upload_json_to_s3, extract_s3_key_from_url, fetch_json_from_s3, get_s3_key_for_audience, ensure_enterprise_audience_folders_exist, get_source_audience_path, list_s3_objects_with_prefix, copy_s3_object, replace_enterprise_in_s3_url
from app.services.summary_service import process_profile_summary
from app.services.openai_service import call_claude_with_retry, split_prompt_into_messages
from app import database

router = APIRouter()


@router.post("/api/v1/audience-rooms")
async def create_audience_room(payload: CreateAudienceRoomRequest):
    """
    Create an audience room, store its description and profile payloads in S3, and persist metadata in Postgres.
    - Stores audience description at: linkedin-audience/{enterpriseName}/{audience_room_id}/description.json
    - Stores each profile payload (with summary=null) at: linkedin-audience/{enterpriseName}/{audience_room_id}/profiles/{profile_id}/profile.json
    - If query and search_results provided: stores search results at: linkedin-audience/{enterpriseName}/{audience_room_id}/indexes.json
    
    Request Body:
    - enterpriseName (optional): Enterprise name to determine which database to use:
        - "gamma" -> uses GAMMA_DATABASE_URL
        - "app" -> uses APP_DATABASE_URL
        - "entelligence" -> uses ENTELLIGENCE_DATABASE_URL
        - "beta" -> uses BETA_DATABASE_URL
        - If not provided, uses AUDIENCE_DATABASE_URL
    """
    logger.info(f"=== CREATE AUDIENCE ROOM REQUEST START ===")
    logger.info(f"Request payload: enterpriseName={payload.enterpriseName}, room_name={payload.audience_room_name}, user_id={payload.userId}")
    logger.info(f"Request payload: profiles_count={len(payload.profiles)}, query={payload.query}, source={payload.source}")
    
    ensure_db_available("audience")
    logger.info("Database availability check passed")
    
    if not s3_client or not s3_bucket:
        logger.error("S3 not configured - missing s3_client or s3_bucket")
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")
    
    logger.info(f"S3 configured: bucket={s3_bucket}, client={'available' if s3_client else 'missing'}")

    room_id = str(uuid.uuid4())
    logger.info(f"Generated room_id: {room_id}")

    # Ensure enterprise and audience type folders exist before uploading files
    logger.info(f"Ensuring enterprise and audience folders exist for enterprise={payload.enterpriseName}, source={payload.source}")
    ensure_enterprise_audience_folders_exist(payload.enterpriseName)

    # Upload audience description to S3
    logger.info(f"Uploading audience description to S3 for room_id={room_id}, enterprise={payload.enterpriseName}, source={payload.source}")
    description_key = get_s3_key_for_audience(room_id, "description.json", payload.enterpriseName, payload.source)
    logger.info(f"Generated S3 key for description: {description_key}")
    description_url = upload_json_to_s3(
        description_key,
        {
            "audience_room_id": room_id,
            "audience_room_name": payload.audience_room_name,
            "description": payload.audience_description,
        },
    )
    logger.info(f"Successfully uploaded description to S3: {description_url}")

    # Handle search results/indexes if provided
    indexes_s3_url = None
    if payload.query and payload.search_results:
        logger.info(f"Uploading indexes/search results to S3 for room_id={room_id}, enterprise={payload.enterpriseName}")
        try:
            # Prepare the indexes/search results JSON
            indexes_data = {
                "audience_room_id": room_id,
                "query": payload.query,
                "total_results": len(payload.search_results),
                "results": payload.search_results,
                "timestamp": datetime.utcnow().isoformat()
            }
            
            # Upload indexes to S3
            indexes_key = get_s3_key_for_audience(room_id, "indexes.json", payload.enterpriseName, payload.source)
            logger.info(f"Generated S3 key for indexes: {indexes_key}")
            indexes_s3_url = upload_json_to_s3(indexes_key, indexes_data)
            logger.info(f"Successfully uploaded search results/indexes to S3 for audience room {room_id}: {indexes_s3_url}")
        except Exception as e:
            logger.error(f"Failed to upload search results to S3 for room_id={room_id}: {e}", exc_info=True)
            # Continue with room creation even if indexes upload fails
    else:
        logger.info(f"No indexes to upload (query={payload.query}, search_results={'present' if payload.search_results else 'missing'})")

    # Build profile records and upload payloads to S3 (summary starts as null)
    logger.info(f"Processing {len(payload.profiles)} profiles for S3 upload and database creation")
    profile_creates = []
    for idx, profile in enumerate(payload.profiles):
        profile_id = str(uuid.uuid4())
        logger.info(f"Processing profile {idx+1}/{len(payload.profiles)}: profile_id={profile_id}, name={profile.name}")
        profile_key = get_s3_key_for_audience(room_id, f"profiles/{profile_id}/profile.json", payload.enterpriseName, payload.source)
        logger.info(f"Generated S3 key for profile: {profile_key}")
        profile_payload = {
            "profile_id": profile_id,
            "audience_room_id": room_id,
            "name": profile.name,
            "age": profile.age,
            "current_company": profile.current_company,
            "current_location": profile.current_location,
            "total_years_experience": profile.total_years_experience,
            "industry": profile.industry,
            "education": profile.education,
            "linkedin_profile_url": profile.linkedin_profile_url,
            "jobTitle": getattr(profile, 'jobTitle', None),
            "headline": getattr(profile, 'headline', None),
            "about": getattr(profile, 'about', None),
            "summary": None,
        }
        logger.info(f"Uploading profile {idx+1} to S3: profile_id={profile_id}")
        profile_url = upload_json_to_s3(profile_key, profile_payload)
        logger.info(f"Successfully uploaded profile {idx+1} to S3: {profile_url}")

        profile_creates.append(
            {
                "id": profile_id,
                "profileName": profile.name,
                "profileUrl": profile.linkedin_profile_url,
                "profileDescriptionS3Url": profile_url,
                "postsS3Url": None,
            }
        )
        logger.info(f"Added profile {idx+1} to profile_creates list")
    
    logger.info(f"Completed S3 uploads: {len(profile_creates)} profiles ready for database insertion")

    try:
        # Log enterprise name for debugging
        logger.info(f"=== CALLING database.create_audience_room ===")
        logger.info(f"Parameters: room_id={room_id}, enterpriseName={payload.enterpriseName}")
        logger.info(f"Parameters: name={payload.audience_room_name}, user_id={payload.userId}")
        logger.info(f"Parameters: description_s3_url={description_url}")
        logger.info(f"Parameters: indexes_s3_url={indexes_s3_url}")
        logger.info(f"Parameters: profiles_data count={len(profile_creates)}")
        
        room = database.create_audience_room(
            room_id=room_id,
            name=payload.audience_room_name,
            description_s3_url=description_url,
            user_id=payload.userId,
            source=payload.source,
            query=payload.query,
            indexes_s3_url=indexes_s3_url,
            profiles_data=profile_creates,
            enterprise_name=payload.enterpriseName,
        )

        logger.info(f"=== database.create_audience_room RETURNED SUCCESSFULLY ===")
        logger.info(f"Returned room: id={room.id}, name={room.name}, profiles_count={len(room.profiles)}")
        logger.info(f"Returned room: descriptionS3Url={room.descriptionS3Url}")
        logger.info(f"Returned room: userId={room.userId}, query={room.query}")

        response_data = {
            "audience_room_id": room.id,
            "audience_room_name": room.name,
            "description_s3_url": room.descriptionS3Url,
            "userId": room.userId,
            "query": room.query,
            "indexes_s3_url": room.indexesS3Url,
            "profiles_created": len(room.profiles),
            "profiles": [
                {
                    "profile_id": p.id,
                    "profile_name": p.profileName,
                    "linkedin_url": p.profileUrl,
                    "profile_description_s3_url": p.profileDescriptionS3Url,
                    "posts_s3_url": p.postsS3Url,
                }
                for p in room.profiles
            ],
        }
        
        logger.info(f"=== CREATE AUDIENCE ROOM REQUEST SUCCESS ===")
        logger.info(f"Response: room_id={room.id}, enterprise={payload.enterpriseName}, profiles={len(room.profiles)}")
        return response_data
        
    except HTTPException as http_ex:
        logger.error(f"HTTPException in create_audience_room: {http_ex.detail}")
        raise
    except Exception as e:
        logger.error(f"=== FATAL ERROR in create_audience_room ===")
        logger.error(f"Error creating audience room: {e}", exc_info=True)
        logger.error(f"Error details: room_id={room_id}, enterprise={payload.enterpriseName}")
        raise HTTPException(status_code=500, detail=f"Failed to create audience room: {str(e)}")


@router.patch("/api/v1/audience-rooms/update-name")
async def update_audience_room_name_endpoint(request: UpdateAudienceRoomNameRequest):
    """
    Update the name of an audience room in the AudienceRoom table.
    
    This endpoint updates only the name field in the AudienceRoom table for the specified audience room.
    
    Request Body:
    - enterpriseName (optional): Enterprise name to determine which database to use:
        - "gamma" -> uses GAMMA_DATABASE_URL
        - "app" -> uses APP_DATABASE_URL
        - "entelligence" -> uses ENTELLIGENCE_DATABASE_URL
        - "beta" -> uses BETA_DATABASE_URL
        - If not provided, uses AUDIENCE_DATABASE_URL
    - newName (required): The new name to set for the audience room
    - audienceRoomId (required): The ID of the audience room to update
    
    Response includes:
    - status: Success status
    - room_id: The audience room ID that was updated
    - new_name: The new name that was set
    - enterpriseName: The enterprise name used (if any)
    
    WARNING: This only modifies the AudienceRoom table, no other tables are touched.
    """
    logger.info(f"=== UPDATE AUDIENCE ROOM NAME REQUEST START ===")
    logger.info(f"Request: room_id={request.audienceRoomId}, new_name={request.newName}, enterprise={request.enterpriseName}")
    
    ensure_db_available("audience")
    logger.info("Database availability check passed")
    
    try:
        logger.info(f"Calling database.update_audience_room: room_id={request.audienceRoomId}, enterprise={request.enterpriseName}")
        updated_room = database.update_audience_room(
            room_id=request.audienceRoomId,
            data={"name": request.newName},
            enterprise_name=request.enterpriseName
        )
        
        if not updated_room:
            logger.warning(f"Audience room {request.audienceRoomId} not found in database (enterprise={request.enterpriseName})")
            raise HTTPException(
                status_code=404,
                detail=f"Audience room {request.audienceRoomId} not found"
            )
        
        logger.info(f"Successfully updated audience room {request.audienceRoomId}: new_name={updated_room.name}")
        logger.info(f"=== UPDATE AUDIENCE ROOM NAME REQUEST SUCCESS ===")
        
        return {
            "status": "success",
            "message": f"Audience room name updated for room {request.audienceRoomId}",
            "room_id": request.audienceRoomId,
            "new_name": request.newName,
            "enterpriseName": request.enterpriseName
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"=== ERROR in update_audience_room_name ===")
        logger.error(f"Error updating audience room name for room {request.audienceRoomId}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update audience room name: {str(e)}"
        )


@router.delete("/api/v1/audience-rooms/{audience_room_id}")
async def delete_audience_room(
    audience_room_id: str = Path(...),
    enterpriseName: Optional[str] = Query(None, description="Enterprise name (gamma, app, entelligence, beta). If not provided, uses default audience database.")
):
    """
    Delete an audience room and all associated data.
    
    This endpoint uses the new folder structure: {enterpriseName}/{audienceType}/{room_id}/
    
    This endpoint:
    1. Extracts S3 URLs from database records and deletes those specific files
    2. Deletes all S3 files matching the folder structure prefix (fallback)
    3. Deletes all profiles from the AudienceProfile table
    4. Deletes the audience room from the AudienceRoom table
    
    Args:
        audience_room_id: The audience room ID
        enterpriseName: Optional enterprise name (gamma, app, entelligence, beta). 
                       If not provided, uses AUDIENCE_DATABASE_URL.
    """
    logger.info(f"=== DELETE AUDIENCE ROOM REQUEST START ===")
    logger.info(f"Request: room_id={audience_room_id}, enterprise={enterpriseName}")
    
    ensure_db_available("audience")
    logger.info("Database availability check passed")
    
    if not s3_client or not s3_bucket:
        logger.error("S3 not configured - missing s3_client or s3_bucket")
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")
    
    logger.info(f"S3 configured: bucket={s3_bucket}")

    try:
        # Fetch audience room with all profiles using enterprise-specific database if provided
        logger.info(f"Fetching audience room {audience_room_id} from database (enterprise={enterpriseName})")
        audience_room = database.find_audience_room_by_id(audience_room_id, include_profiles=True, enterprise_name=enterpriseName)
        
        if not audience_room:
            logger.warning(f"Audience room {audience_room_id} not found in database (enterprise={enterpriseName})")
            raise HTTPException(status_code=404, detail=f"Audience room {audience_room_id} not found")
        
        logger.info(f"Found audience room: id={audience_room.id}, name={audience_room.name}")
        profiles = audience_room.profiles
        profile_count = len(profiles)
        logger.info(f"Room has {profile_count} profiles to delete")
        
        # Collect all S3 keys from database records
        from app.utils.s3_utils import get_audience_type_from_source
        s3_keys_from_db = set()  # Use set to avoid duplicates
        
        # Extract S3 keys from audience room
        if audience_room.descriptionS3Url:
            key = extract_s3_key_from_url(audience_room.descriptionS3Url)
            if key:
                s3_keys_from_db.add(key)
                logger.info(f"Found room description S3 URL: {key}")
        
        if audience_room.indexesS3Url:
            key = extract_s3_key_from_url(audience_room.indexesS3Url)
            if key:
                s3_keys_from_db.add(key)
                logger.info(f"Found room indexes S3 URL: {key}")
        
        # Extract S3 keys from all profiles
        for profile in profiles:
            if profile.profileDescriptionS3Url:
                key = extract_s3_key_from_url(profile.profileDescriptionS3Url)
                if key:
                    s3_keys_from_db.add(key)
            
            if profile.postsS3Url:
                key = extract_s3_key_from_url(profile.postsS3Url)
                if key:
                    s3_keys_from_db.add(key)
            
            if profile.commentsS3Url:
                key = extract_s3_key_from_url(profile.commentsS3Url)
                if key:
                    s3_keys_from_db.add(key)
        
        logger.info(f"Extracted {len(s3_keys_from_db)} unique S3 keys from database records")
        
        # Delete S3 files using new folder structure prefix (fallback to catch any orphaned files)
        normalized_enterprise = enterpriseName.lower().strip() if enterpriseName else "default"
        audience_type = get_audience_type_from_source(audience_room.source)
        s3_prefix = f"{normalized_enterprise}/{audience_type}/{audience_room_id}/"
        logger.info(f"Using S3 prefix for fallback deletion: {s3_prefix} (enterprise={normalized_enterprise}, audience_type={audience_type}, source={audience_room.source})")
        
        deleted_s3_files = []
        objects_to_delete = []
        
        # Add all keys from database to deletion list
        for key in s3_keys_from_db:
            objects_to_delete.append({'Key': key})
            deleted_s3_files.append(key)
        
        try:
            # Also list all objects with the prefix to catch any files not in database
            logger.info(f"Listing S3 objects with prefix: {s3_prefix}")
            paginator = s3_client.get_paginator('list_objects_v2')
            pages = paginator.paginate(Bucket=s3_bucket, Prefix=s3_prefix)
            
            prefix_keys = set()
            for page in pages:
                if 'Contents' in page:
                    for obj in page['Contents']:
                        prefix_keys.add(obj['Key'])
            
            logger.info(f"Found {len(prefix_keys)} S3 objects with prefix {s3_prefix}")
            
            # Add prefix-based keys that weren't already in database keys
            additional_keys = prefix_keys - s3_keys_from_db
            additional_count = len(additional_keys)
            if additional_keys:
                logger.info(f"Found {additional_count} additional S3 objects not in database records")
                for key in additional_keys:
                    objects_to_delete.append({'Key': key})
                    deleted_s3_files.append(key)
            
            logger.info(f"Total {len(objects_to_delete)} S3 objects to delete ({len(s3_keys_from_db)} from DB, {additional_count} from prefix)")
            
            # Delete all objects in batch (max 1000 objects per request)
            if objects_to_delete:
                batch_count = (len(objects_to_delete) + 999) // 1000
                logger.info(f"Deleting {len(objects_to_delete)} objects in {batch_count} batch(es)")
                for i in range(0, len(objects_to_delete), 1000):
                    batch = objects_to_delete[i:i+1000]
                    batch_num = (i // 1000) + 1
                    logger.info(f"Deleting batch {batch_num}/{batch_count} ({len(batch)} objects)")
                    s3_client.delete_objects(
                        Bucket=s3_bucket,
                        Delete={
                            'Objects': batch,
                            'Quiet': True
                        }
                    )
                logger.info(f"Successfully deleted {len(objects_to_delete)} S3 objects for audience room {audience_room_id}")
            else:
                logger.warning(f"No S3 objects found for audience room {audience_room_id}")
                
        except Exception as e:
            logger.error(f"Error deleting S3 files for audience room {audience_room_id}: {e}", exc_info=True)
            # Continue with database deletion even if S3 deletion fails
        
        # Delete all profiles from database first using enterprise-specific database if provided
        # Use bulk delete for efficiency
        logger.info(f"Deleting all profiles for room {audience_room_id} from database (enterprise={enterpriseName})")
        deleted_profile_count = database.delete_audience_profiles_by_room(audience_room_id, enterprise_name=enterpriseName)
        logger.info(f"Successfully deleted {deleted_profile_count} profiles from database")
        
        # Delete the audience room from database using enterprise-specific database if provided
        logger.info(f"Deleting audience room {audience_room_id} from database (enterprise={enterpriseName})")
        room_deleted = database.delete_audience_room(audience_room_id, enterprise_name=enterpriseName)
        if not room_deleted:
            logger.warning(f"Audience room {audience_room_id} was not found or could not be deleted")
            raise HTTPException(status_code=404, detail=f"Audience room {audience_room_id} not found or could not be deleted")
        logger.info(f"Successfully deleted audience room {audience_room_id} from database")
        
        logger.info(f"=== DELETE AUDIENCE ROOM REQUEST SUCCESS ===")
        logger.info(f"Deleted: room_id={audience_room_id}, profiles={deleted_profile_count}, s3_files={len(deleted_s3_files)}")
        
        return {
            "message": f"Audience room {audience_room_id} deleted successfully",
            "deleted_room_id": audience_room_id,
            "deleted_profiles": deleted_profile_count,
            "deleted_s3_files": len(deleted_s3_files),
            "s3_files_from_db": len(s3_keys_from_db),
            "s3_prefix_used": s3_prefix
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"=== ERROR in delete_audience_room ===")
        logger.error(f"Error deleting audience room {audience_room_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to delete audience room: {str(e)}")


@router.get("/api/v1/audience-rooms/{audience_room_id}/description")
async def get_audience_room_description(
    audience_room_id: str = Path(...),
    enterpriseName: Optional[str] = Query(None, description="Enterprise name (gamma, app, entelligence, beta). If not provided, uses default audience database.")
):
    """Fetch and return the audience room description JSON from S3.
    
    Args:
        audience_room_id: The audience room ID
        enterpriseName: Optional enterprise name (gamma, app, entelligence, beta). 
                       If not provided, uses AUDIENCE_DATABASE_URL.
    """
    logger.info(f"=== GET AUDIENCE ROOM DESCRIPTION REQUEST START ===")
    logger.info(f"Request: room_id={audience_room_id}, enterprise={enterpriseName}")
    
    ensure_db_available("audience")
    if not s3_client or not s3_bucket:
        logger.error("S3 not configured")
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")
    
    try:
        # Verify room exists using enterprise-specific database if provided
        logger.info(f"Fetching audience room {audience_room_id} from database (enterprise={enterpriseName})")
        room = database.find_audience_room_by_id(audience_room_id, enterprise_name=enterpriseName)
        if not room:
            logger.warning(f"Audience room {audience_room_id} not found (enterprise={enterpriseName})")
            raise HTTPException(status_code=404, detail=f"Audience room {audience_room_id} not found")
        
        logger.info(f"Found audience room: id={room.id}, name={room.name}")
        
        if not room.descriptionS3Url:
            logger.warning(f"Description S3 URL not found for room {audience_room_id}")
            raise HTTPException(status_code=404, detail="Description not found for this audience room")
        
        # Extract S3 key from URL
        logger.info(f"Extracting S3 key from URL: {room.descriptionS3Url}")
        description_key = extract_s3_key_from_url(room.descriptionS3Url)
        if not description_key:
            logger.error(f"Failed to extract S3 key from URL: {room.descriptionS3Url}")
            raise HTTPException(status_code=500, detail="Invalid S3 URL format")
        
        logger.info(f"Extracted S3 key: {description_key}")
        
        # Fetch JSON from S3
        logger.info(f"Fetching description JSON from S3: {description_key}")
        description_data = fetch_json_from_s3(description_key)
        logger.info(f"Successfully fetched description from S3 for room {audience_room_id}")
        logger.info(f"=== GET AUDIENCE ROOM DESCRIPTION REQUEST SUCCESS ===")
        return description_data
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"=== ERROR in get_audience_room_description ===")
        logger.error(f"Error fetching description for audience room {audience_room_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch description")


@router.get("/api/v1/audience-rooms/{audience_room_id}/profiles/{profile_id}/description")
async def get_profile_description(
    audience_room_id: str = Path(...),
    profile_id: str = Path(...),
    enterpriseName: Optional[str] = Query(None, description="Enterprise name (gamma, app, entelligence, beta). If not provided, uses default audience database.")
):
    """Fetch and return the profile description JSON from S3.
    
    Args:
        audience_room_id: The audience room ID
        profile_id: The profile ID
        enterpriseName: Optional enterprise name (gamma, app, entelligence, beta). 
                       If not provided, uses AUDIENCE_DATABASE_URL.
    """
    logger.info(f"=== GET PROFILE DESCRIPTION REQUEST START ===")
    logger.info(f"Request: room_id={audience_room_id}, profile_id={profile_id}, enterprise={enterpriseName}")
    
    ensure_db_available("audience")
    if not s3_client or not s3_bucket:
        logger.error("S3 not configured")
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")
    
    try:
        # Verify profile exists and belongs to the room using enterprise-specific database if provided
        logger.info(f"Fetching profile {profile_id} from database (enterprise={enterpriseName})")
        profile = database.find_audience_profile_by_id(profile_id, include_room=True, enterprise_name=enterpriseName)
        if not profile:
            logger.warning(f"Profile {profile_id} not found (enterprise={enterpriseName})")
            raise HTTPException(status_code=404, detail=f"Profile {profile_id} not found")
        
        logger.info(f"Found profile: id={profile.id}, name={profile.profileName}, room_id={profile.audienceRoomId}")
        
        if profile.audienceRoomId != audience_room_id:
            logger.warning(f"Profile {profile_id} belongs to room {profile.audienceRoomId}, not {audience_room_id}")
            raise HTTPException(status_code=404, detail=f"Profile {profile_id} does not belong to audience room {audience_room_id}")
        
        if not profile.profileDescriptionS3Url:
            logger.warning(f"Profile description S3 URL not found for profile {profile_id}")
            raise HTTPException(status_code=404, detail="Profile description not found")
        
        # Extract S3 key from URL
        logger.info(f"Extracting S3 key from URL: {profile.profileDescriptionS3Url}")
        profile_key = extract_s3_key_from_url(profile.profileDescriptionS3Url)
        if not profile_key:
            logger.error(f"Failed to extract S3 key from URL: {profile.profileDescriptionS3Url}")
            raise HTTPException(status_code=500, detail="Invalid S3 URL format")
        
        logger.info(f"Extracted S3 key: {profile_key}")
        
        # Fetch JSON from S3
        logger.info(f"Fetching profile description JSON from S3: {profile_key}")
        profile_data = fetch_json_from_s3(profile_key)
        logger.info(f"Successfully fetched profile description from S3 for profile {profile_id}")
        logger.info(f"=== GET PROFILE DESCRIPTION REQUEST SUCCESS ===")
        return profile_data
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"=== ERROR in get_profile_description ===")
        logger.error(f"Error fetching profile description for {profile_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch profile description")


@router.get("/api/v1/audience-rooms/{audience_room_id}/profiles/{profile_id}/posts")
async def get_profile_posts(
    audience_room_id: str = Path(...),
    profile_id: str = Path(...),
    enterpriseName: Optional[str] = Query(None, description="Enterprise name (gamma, app, entelligence, beta). If not provided, uses default audience database.")
):
    """Fetch and return the profile posts JSON from S3.
    
    Args:
        audience_room_id: The audience room ID
        profile_id: The profile ID
        enterpriseName: Optional enterprise name (gamma, app, entelligence, beta). 
                       If not provided, uses AUDIENCE_DATABASE_URL.
    """
    logger.info(f"=== GET PROFILE POSTS REQUEST START ===")
    logger.info(f"Request: room_id={audience_room_id}, profile_id={profile_id}, enterprise={enterpriseName}")
    
    ensure_db_available("audience")
    if not s3_client or not s3_bucket:
        logger.error("S3 not configured")
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")
    
    try:
        # Verify profile exists and belongs to the room using enterprise-specific database if provided
        logger.info(f"Fetching profile {profile_id} from database (enterprise={enterpriseName})")
        profile = database.find_audience_profile_by_id(profile_id, include_room=True, enterprise_name=enterpriseName)
        if not profile:
            logger.warning(f"Profile {profile_id} not found (enterprise={enterpriseName})")
            raise HTTPException(status_code=404, detail=f"Profile {profile_id} not found")
        
        logger.info(f"Found profile: id={profile.id}, name={profile.profileName}")
        
        if profile.audienceRoomId != audience_room_id:
            logger.warning(f"Profile {profile_id} belongs to room {profile.audienceRoomId}, not {audience_room_id}")
            raise HTTPException(status_code=404, detail=f"Profile {profile_id} does not belong to audience room {audience_room_id}")
        
        if not profile.postsS3Url:
            logger.warning(f"Posts S3 URL not found for profile {profile_id}")
            raise HTTPException(status_code=404, detail="Posts not found for this profile")
        
        # Extract S3 key from URL
        logger.info(f"Extracting S3 key from URL: {profile.postsS3Url}")
        posts_key = extract_s3_key_from_url(profile.postsS3Url)
        if not posts_key:
            logger.error(f"Failed to extract S3 key from URL: {profile.postsS3Url}")
            raise HTTPException(status_code=500, detail="Invalid S3 URL format")
        
        logger.info(f"Extracted S3 key: {posts_key}")
        
        # Fetch JSON from S3
        logger.info(f"Fetching posts JSON from S3: {posts_key}")
        posts_data = fetch_json_from_s3(posts_key)
        logger.info(f"Successfully fetched posts from S3 for profile {profile_id}")
        logger.info(f"=== GET PROFILE POSTS REQUEST SUCCESS ===")
        return posts_data
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"=== ERROR in get_profile_posts ===")
        logger.error(f"Error fetching posts for profile {profile_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch posts")


@router.get("/api/v1/audience-rooms/{audience_room_id}/indexes")
async def get_audience_room_indexes(
    audience_room_id: str = Path(...),
    enterpriseName: Optional[str] = Query(None, description="Enterprise name (gamma, app, entelligence, beta). If not provided, uses default audience database.")
):
    """Fetch and return the audience room indexes JSON from S3.
    
    Args:
        audience_room_id: The audience room ID
        enterpriseName: Optional enterprise name (gamma, app, entelligence, beta). 
                       If not provided, uses AUDIENCE_DATABASE_URL.
    """
    logger.info(f"=== GET AUDIENCE ROOM INDEXES REQUEST START ===")
    logger.info(f"Request: room_id={audience_room_id}, enterprise={enterpriseName}")
    
    ensure_db_available("audience")
    if not s3_client or not s3_bucket:
        logger.error("S3 not configured")
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")
    
    try:
        # Verify room exists using enterprise-specific database if provided
        logger.info(f"Fetching audience room {audience_room_id} from database (enterprise={enterpriseName})")
        room = database.find_audience_room_by_id(audience_room_id, enterprise_name=enterpriseName)
        if not room:
            logger.warning(f"Audience room {audience_room_id} not found (enterprise={enterpriseName})")
            raise HTTPException(status_code=404, detail=f"Audience room {audience_room_id} not found")
        
        logger.info(f"Found audience room: id={room.id}, name={room.name}")
        
        if not room.indexesS3Url:
            logger.warning(f"Indexes S3 URL not found for room {audience_room_id}")
            raise HTTPException(status_code=404, detail="Indexes not found for this audience room")
        
        # Extract S3 key from URL
        logger.info(f"Extracting S3 key from URL: {room.indexesS3Url}")
        indexes_key = extract_s3_key_from_url(room.indexesS3Url)
        if not indexes_key:
            logger.error(f"Failed to extract S3 key from URL: {room.indexesS3Url}")
            raise HTTPException(status_code=500, detail="Invalid S3 URL format")
        
        logger.info(f"Extracted S3 key: {indexes_key}")
        
        # Fetch JSON from S3
        logger.info(f"Fetching indexes JSON from S3: {indexes_key}")
        indexes_data = fetch_json_from_s3(indexes_key)
        logger.info(f"Successfully fetched indexes from S3 for room {audience_room_id}")
        logger.info(f"=== GET AUDIENCE ROOM INDEXES REQUEST SUCCESS ===")
        return indexes_data
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"=== ERROR in get_audience_room_indexes ===")
        logger.error(f"Error fetching indexes for audience room {audience_room_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch indexes")


@router.get("/api/v1/audience-rooms/{audience_room_id}/profiles/{profile_id}/comments")
async def get_profile_comments(
    audience_room_id: str = Path(...),
    profile_id: str = Path(...),
    enterpriseName: Optional[str] = Query(None, description="Enterprise name (gamma, app, entelligence, beta). If not provided, uses default audience database.")
):
    """Fetch and return the profile comments JSON from S3.
    
    Args:
        audience_room_id: The audience room ID
        profile_id: The profile ID
        enterpriseName: Optional enterprise name (gamma, app, entelligence, beta). 
                       If not provided, uses AUDIENCE_DATABASE_URL.
    """
    logger.info(f"=== GET PROFILE COMMENTS REQUEST START ===")
    logger.info(f"Request: room_id={audience_room_id}, profile_id={profile_id}, enterprise={enterpriseName}")
    
    ensure_db_available("audience")
    if not s3_client or not s3_bucket:
        logger.error("S3 not configured")
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")
    
    try:
        # Verify profile exists and belongs to the room using enterprise-specific database if provided
        logger.info(f"Fetching profile {profile_id} from database (enterprise={enterpriseName})")
        profile = database.find_audience_profile_by_id(profile_id, include_room=True, enterprise_name=enterpriseName)
        if not profile:
            logger.warning(f"Profile {profile_id} not found (enterprise={enterpriseName})")
            raise HTTPException(status_code=404, detail=f"Profile {profile_id} not found")
        
        logger.info(f"Found profile: id={profile.id}, name={profile.profileName}")
        
        if profile.audienceRoomId != audience_room_id:
            logger.warning(f"Profile {profile_id} belongs to room {profile.audienceRoomId}, not {audience_room_id}")
            raise HTTPException(status_code=404, detail=f"Profile {profile_id} does not belong to audience room {audience_room_id}")
        
        if not profile.commentsS3Url:
            logger.warning(f"Comments S3 URL not found for profile {profile_id}")
            raise HTTPException(status_code=404, detail="Comments not found for this profile")
        
        # Extract S3 key from URL
        logger.info(f"Extracting S3 key from URL: {profile.commentsS3Url}")
        comments_key = extract_s3_key_from_url(profile.commentsS3Url)
        if not comments_key:
            logger.error(f"Failed to extract S3 key from URL: {profile.commentsS3Url}")
            raise HTTPException(status_code=500, detail="Invalid S3 URL format")
        
        logger.info(f"Extracted S3 key: {comments_key}")
        
        # Fetch JSON from S3
        logger.info(f"Fetching comments JSON from S3: {comments_key}")
        comments_data = fetch_json_from_s3(comments_key)
        logger.info(f"Successfully fetched comments from S3 for profile {profile_id}")
        logger.info(f"=== GET PROFILE COMMENTS REQUEST SUCCESS ===")
        return comments_data
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"=== ERROR in get_profile_comments ===")
        logger.error(f"Error fetching comments for profile {profile_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch comments")
@router.post("/api/v1/audience-rooms/{audience_room_id}/generate-group-summary")
async def generate_group_summary(
    audience_room_id: str = Path(...),
    enterpriseName: Optional[str] = Query(None, description="Enterprise name (gamma, app, entelligence, beta). If not provided, uses default audience database.")
):
    """
    Generate a group summary and traits for an audience room based on all profile summaries.
    
    Flow:
    1. Fetch all profiles in the audience room
    2. For each profile, fetch description JSON from S3 and extract the summary
    3. Combine all profile summaries
    4. Generate a group summary using OpenAI based on the combined summaries
    5. Generate traits (5 traits with keywordTags and descriptions) based on profile summaries
    6. Update the audience room description JSON in S3 with both summary and traits fields
    
    Query Parameters:
    - enterpriseName (optional): Enterprise name to determine which database to use:
        - "gamma" -> uses GAMMA_DATABASE_URL
        - "app" -> uses APP_DATABASE_URL
        - "entelligence" -> uses ENTELLIGENCE_DATABASE_URL
        - "beta" -> uses BETA_DATABASE_URL
        - If not provided, uses AUDIENCE_DATABASE_URL
    """
    ensure_db_available("audience")
    from app.config import openai_client, anthropic_client
    if not anthropic_client:
        raise HTTPException(status_code=503, detail="Anthropic client not initialized. Please set ANTHROPIC_API_KEY.")
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")
    
    try:
        # Fetch audience room and profiles
        audience_room = database.find_audience_room_by_id(audience_room_id, include_profiles=True, enterprise_name=enterpriseName)
        if not audience_room:
            raise HTTPException(status_code=404, detail=f"Audience room {audience_room_id} not found")
        
        profiles = audience_room.profiles
        if not profiles:
            raise HTTPException(status_code=404, detail=f"No profiles found in audience room {audience_room_id}")
        
        # Fetch audience room description JSON from S3
        if not audience_room.descriptionS3Url:
            raise HTTPException(status_code=404, detail="Description not found for this audience room")
        
        description_key = extract_s3_key_from_url(audience_room.descriptionS3Url)
        if not description_key:
            raise HTTPException(status_code=500, detail="Invalid S3 URL format for audience room description")
        
        room_description_data = fetch_json_from_s3(description_key)
        
        logger.info(f"Generating group summary for {len(profiles)} profiles in audience room {audience_room_id}")
        
        # Fetch profile summaries from S3
        profile_summaries = []
        companies = set()
        profiles_processed = 0
        profiles_skipped = 0
        
        for profile in profiles:
            try:
                if not profile.profileDescriptionS3Url:
                    logger.warning(f"Profile {profile.id} has no description URL, skipping")
                    profiles_skipped += 1
                    continue
                
                profile_key = extract_s3_key_from_url(profile.profileDescriptionS3Url)
                if not profile_key:
                    logger.warning(f"Profile {profile.id} has invalid description URL, skipping")
                    profiles_skipped += 1
                    continue
                
                profile_data = fetch_json_from_s3(profile_key)
                profile_summary = profile_data.get("summary")
                
                if not profile_summary:
                    logger.warning(f"Profile {profile.id} has no summary, skipping")
                    profiles_skipped += 1
                    continue
                
                profile_summaries.append({
                    "name": profile.profileName,
                    "summary": profile_summary,
                    "company": profile_data.get("current_company")
                })
                
                # Collect company information
                if profile_data.get("current_company"):
                    companies.add(profile_data.get("current_company"))
                
                profiles_processed += 1
                
            except Exception as e:
                logger.error(f"Error fetching profile {profile.id} description: {e}")
                profiles_skipped += 1
                continue
        
        if not profile_summaries:
            raise HTTPException(
                status_code=400, 
                detail="No profile summaries found. Please generate profile summaries first using /api/v1/audience-rooms/{audience_room_id}/generate-summaries/async"
            )
        
        # Combine all profile summaries
        combined_summaries = "\n\n".join([
            f"{idx + 1}. {p['name']} ({p.get('company', 'N/A')}):\n{p['summary']}"
            for idx, p in enumerate(profile_summaries)
        ])
        
        # Determine company type/context
        company_list = ", ".join(sorted(companies)) if companies else "various companies"
        company_type = company_list if len(companies) <= 3 else f"{len(companies)} companies"
        
        # Use group_summary_prompt from LangSmith
        from prompts import group_summary_prompt
        
        full_group_prompt = group_summary_prompt.format(
            total_profiles=len(profile_summaries),
            company_type=company_type,
            company_list=company_list,
            combined_summaries=combined_summaries
        )
        
        # Split the prompt into system and user messages
        default_group_system = "You are an expert at analyzing groups of LinkedIn profiles and generating comprehensive, insightful high-level summaries. Write detailed, informative summaries that capture collective patterns and insights."
        system_message, user_prompt = split_prompt_into_messages(full_group_prompt, default_group_system)
        
        # Generate group summary using Claude Sonnet
        try:
            group_summary = await call_claude_with_retry(
                context_id=audience_room_id,
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": user_prompt}
                ],
                max_tokens=1200,
                max_retries=3,
                initial_delay=1.0,
                model="claude-sonnet-4-5-20250929"  # Specific snapshot for production stability (format: claude-sonnet-4-5-YYYYMMDD)
            )
            
            group_summary = group_summary.strip()
            
            # Generate traits based on profile summaries using LangSmith prompt
            import json
            from prompts import traits_generation_prompt
            
            full_traits_prompt = traits_generation_prompt.format(
                total_profiles=len(profile_summaries),
                combined_summaries=combined_summaries
            )
            
            # Split the prompt into system and user messages
            default_traits_system = "You are an expert at analyzing professional profiles and generating structured trait data. Always return valid JSON only, no additional text."
            traits_system_message, traits_prompt = split_prompt_into_messages(full_traits_prompt, default_traits_system)
            
            try:
                # Generate traits using Claude Sonnet
                traits_response = await call_claude_with_retry(
                    context_id=audience_room_id,
                    messages=[
                        {"role": "system", "content": traits_system_message},
                        {"role": "user", "content": traits_prompt}
                    ],
                    max_tokens=2000,
                    max_retries=3,
                    initial_delay=1.0,
                    model="claude-sonnet-4-5-20250929"  # Specific snapshot for production stability
                )
                
                traits_response = traits_response.strip()
                
                # Parse the JSON response
                if "```json" in traits_response:
                    traits_response = traits_response.split("```json")[1].split("```")[0].strip()
                elif "```" in traits_response:
                    traits_response = traits_response.split("```")[1].split("```")[0].strip()
                
                traits_data = json.loads(traits_response)
                
                # Validate that we have the expected structure
                if not isinstance(traits_data, dict) or "traits" not in traits_data:
                    raise ValueError("Invalid traits JSON structure: missing 'traits' key")
                
                if not isinstance(traits_data["traits"], list) or len(traits_data["traits"]) != 5:
                    raise ValueError(f"Invalid traits JSON structure: expected 5 traits, got {len(traits_data.get('traits', []))}")
                
                # Validate each trait has the required fields
                required_titles = [
                    "Skills & Expertise",
                    "Working Style",
                    "Motivations & Values",
                    "Pain Points & Needs",
                    "Organizational Leadership & Psychographic Profile"
                ]
                
                received_titles = [trait.get("title") for trait in traits_data["traits"]]
                if set(received_titles) != set(required_titles):
                    raise ValueError(f"Invalid trait titles. Expected: {required_titles}, Got: {received_titles}")
                
                for trait in traits_data["traits"]:
                    if "keywordTags" not in trait or "descriptions" not in trait:
                        raise ValueError(f"Trait '{trait.get('title')}' missing required fields")
                    if not isinstance(trait["keywordTags"], list) or not isinstance(trait["descriptions"], list):
                        raise ValueError(f"Trait '{trait.get('title')}' has invalid keywordTags or descriptions format")
                    if len(trait["keywordTags"]) != len(trait["descriptions"]):
                        raise ValueError(f"Trait '{trait.get('title')}' has mismatched keywordTags and descriptions counts")
                
                logger.info(f"Successfully generated traits for audience room {audience_room_id}")
                
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse traits JSON: {e}")
                raise HTTPException(status_code=500, detail=f"Failed to parse traits JSON: {str(e)}")
            except ValueError as e:
                logger.error(f"Invalid traits structure: {e}")
                raise HTTPException(status_code=500, detail=f"Invalid traits structure: {str(e)}")
            except Exception as e:
                logger.error(f"Error generating traits: {e}")
                raise HTTPException(status_code=500, detail=f"Failed to generate traits: {str(e)}")
            
            # Update audience room description JSON with the summary and traits
            room_description_data["summary"] = group_summary
            room_description_data["traits"] = traits_data["traits"]
            
            # Upload updated description back to S3
            updated_description_url = upload_json_to_s3(description_key, room_description_data)
            
            # Update the audience room record with the new URL
            database.update_audience_room(audience_room_id, {"descriptionS3Url": updated_description_url}, enterprise_name=enterpriseName)
            
            return {
                "audience_room_id": audience_room_id,
                "audience_room_name": audience_room.name,
                "summary": group_summary,
                "traits": traits_data["traits"],
                "total_profiles": len(profiles),
                "profiles_processed": profiles_processed,
                "profiles_skipped": profiles_skipped,
                "companies_represented": list(companies),
                "description_s3_url": updated_description_url
            }
            
        except Exception as e:
            logger.error(f"Error generating group summary: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to generate group summary: {str(e)}")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating group summary: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to generate group summary: {str(e)}")


@router.post("/api/v1/audience-rooms/{audience_room_id}/remove-labels")
async def remove_labels_from_posts(
    audience_room_id: str = Path(...),
    enterpriseName: Optional[str] = Query(None, description="Enterprise name (gamma, app, entelligence, beta). If not provided, uses default audience database.")
):
    """
    Remove the 'labels' field from all posts JSON for all profiles in an audience room.
    
    Flow:
    1. Fetch all profiles in the audience room
    2. For each profile with postsS3Url:
       - Fetch posts JSON from S3
       - Remove 'labels' field from each post
       - Upload updated JSON back to S3
       - Update the profile record in the database
    
    Query Parameters:
    - enterpriseName (optional): Enterprise name to determine which database to use:
        - "gamma" -> uses GAMMA_DATABASE_URL
        - "app" -> uses APP_DATABASE_URL
        - "entelligence" -> uses ENTELLIGENCE_DATABASE_URL
        - "beta" -> uses BETA_DATABASE_URL
        - If not provided, uses AUDIENCE_DATABASE_URL
    """
    ensure_db_available("audience")
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")
    
    try:
        # Fetch audience room and profiles
        audience_room = database.find_audience_room_by_id(audience_room_id, include_profiles=True, enterprise_name=enterpriseName)
        if not audience_room:
            raise HTTPException(status_code=404, detail=f"Audience room {audience_room_id} not found")
        
        profiles = audience_room.profiles
        if not profiles:
            raise HTTPException(status_code=404, detail=f"No profiles found in audience room {audience_room_id}")
        
        logger.info(f"Removing labels from posts for {len(profiles)} profiles in audience room {audience_room_id}")
        
        processed_profiles = []
        total_posts_updated = 0
        profiles_skipped = 0
        profiles_with_errors = 0
        
        for profile in profiles:
            profile_id = profile.id
            profile_name = profile.profileName
            
            # Skip if no posts URL
            if not profile.postsS3Url:
                logger.warning(f"Profile {profile_id} ({profile_name}) has no posts URL, skipping")
                profiles_skipped += 1
                processed_profiles.append({
                    "profile_id": profile_id,
                    "profile_name": profile_name,
                    "status": "skipped",
                    "reason": "no_posts_url",
                    "posts_updated": 0
                })
                continue
            
            try:
                # Extract S3 key and fetch posts
                posts_key = extract_s3_key_from_url(profile.postsS3Url)
                if not posts_key:
                    logger.error(f"Invalid S3 URL format for profile {profile_id}: {profile.postsS3Url}")
                    profiles_with_errors += 1
                    processed_profiles.append({
                        "profile_id": profile_id,
                        "profile_name": profile_name,
                        "status": "error",
                        "reason": "invalid_s3_url",
                        "posts_updated": 0
                    })
                    continue
                
                # Fetch posts JSON from S3
                posts_data = fetch_json_from_s3(posts_key)
                
                # Extract posts array (could be in different formats)
                posts = []
                original_structure = None
                if isinstance(posts_data, dict):
                    posts = posts_data.get("posts", [])
                    if not posts and isinstance(posts_data.get("data"), list):
                        posts = posts_data["data"]
                    original_structure = posts_data
                elif isinstance(posts_data, list):
                    posts = posts_data
                    original_structure = posts
                
                if not posts:
                    logger.warning(f"No posts found for profile {profile_id}")
                    profiles_skipped += 1
                    processed_profiles.append({
                        "profile_id": profile_id,
                        "profile_name": profile_name,
                        "status": "skipped",
                        "reason": "no_posts",
                        "posts_updated": 0
                    })
                    continue
                
                # Remove 'labels' field from each post
                posts_updated_count = 0
                for post in posts:
                    if isinstance(post, dict) and "labels" in post:
                        del post["labels"]
                        posts_updated_count += 1
                
                if posts_updated_count == 0:
                    logger.info(f"No labels found in posts for profile {profile_id}")
                    processed_profiles.append({
                        "profile_id": profile_id,
                        "profile_name": profile_name,
                        "status": "skipped",
                        "reason": "no_labels_found",
                        "posts_updated": 0
                    })
                    continue
                
                # Update the posts data structure
                if isinstance(original_structure, dict):
                    original_structure["posts"] = posts
                    updated_posts_data = original_structure
                else:
                    updated_posts_data = posts
                
                # Upload updated posts back to S3
                updated_posts_url = upload_json_to_s3(posts_key, updated_posts_data)
                
                # Update profile record with new posts URL
                database.update_audience_profile(profile_id, {"postsS3Url": updated_posts_url}, enterprise_name=enterpriseName)
                
                total_posts_updated += posts_updated_count
                processed_profiles.append({
                    "profile_id": profile_id,
                    "profile_name": profile_name,
                    "status": "success",
                    "posts_updated": posts_updated_count,
                    "updated_posts_url": updated_posts_url
                })
                
                logger.info(f"Removed labels from {posts_updated_count} posts for profile {profile_id}")
                
            except Exception as e:
                logger.error(f"Error processing profile {profile_id}: {e}")
                profiles_with_errors += 1
                processed_profiles.append({
                    "profile_id": profile_id,
                    "profile_name": profile_name,
                    "status": "error",
                    "reason": str(e),
                    "posts_updated": 0
                })
        
        return {
            "audience_room_id": audience_room_id,
            "audience_room_name": audience_room.name,
            "total_profiles": len(profiles),
            "total_posts_updated": total_posts_updated,
            "profiles_processed": len([p for p in processed_profiles if p["status"] == "success"]),
            "profiles_skipped": profiles_skipped,
            "profiles_with_errors": profiles_with_errors,
            "profiles": processed_profiles
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error removing labels from posts: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to remove labels from posts: {str(e)}")


@router.post("/api/v1/audience-rooms/{audience_room_id}/copy-to-client")
async def copy_audience_room_to_client(
    audience_room_id: str = Path(..., description="The audience room ID to copy"),
    payload: CopyAudienceRoomToClientRequest = ...,
):
    """
    Copy an audience room and its profiles from a source enterprise
    to a target enterprise, including all related S3 assets.
    """
    logger.info("=== COPY AUDIENCE ROOM TO CLIENT REQUEST START ===")
    logger.info(
        "Request: room_id=%s, source=%s, target=%s",
        audience_room_id,
        payload.sourceEnterpriseName,
        payload.targetEnterpriseName,
    )

    ensure_db_available("audience")
    logger.info("Database availability check passed")

    if not s3_client or not s3_bucket:
        logger.error("S3 not configured")
        raise HTTPException(
            status_code=503,
            detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.",
        )

    # Normalize enterprise names
    source_enterprise = (
        payload.sourceEnterpriseName.lower().strip()
        if payload.sourceEnterpriseName
        else "gamma"
    )
    target_enterprise = payload.targetEnterpriseName.lower().strip()

    valid_enterprises = {"gamma", "app", "entelligence", "beta"}

    if target_enterprise not in valid_enterprises:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid target enterprise: {target_enterprise}. "
                f"Must be one of: {', '.join(valid_enterprises)}"
            ),
        )

    if source_enterprise == target_enterprise:
        raise HTTPException(
            status_code=400,
            detail=f"Source and target enterprises cannot be the same: {source_enterprise}",
        )

    try:
        # ---------------------------------------------------------------------
        # Step 1: Fetch room + profiles from source DB
        # ---------------------------------------------------------------------
        logger.info(
            "Fetching audience room %s from source enterprise=%s",
            audience_room_id,
            source_enterprise,
        )

        source_room = database.find_audience_room_by_id(
            audience_room_id,
            include_profiles=True,
            enterprise_name=source_enterprise,
        )

        if not source_room:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Audience room {audience_room_id} not found "
                    f"in source enterprise '{source_enterprise}'"
                ),
            )

        logger.info(
            "Found audience room id=%s name=%s profiles=%d",
            source_room.id,
            source_room.name,
            len(source_room.profiles),
        )

        # Get source userId from source room
        source_user_id = source_room.userId
        if not source_user_id:
            logger.warning(
                "Source audience room %s has no userId, DynamoDB replication will be skipped",
                audience_room_id
            )
        else:
            logger.info("Source userId resolved: %s", source_user_id)
        logger.info(
            "Checking if audience room %s already exists in target enterprise=%s",
            audience_room_id,
            target_enterprise,
        )
        
        existing_target_room = database.find_audience_room_by_id(
            audience_room_id,
            include_profiles=False,
            enterprise_name=target_enterprise,
        )
        
        if existing_target_room:
            logger.warning(
                "Audience room %s already exists in target enterprise=%s",
                audience_room_id,
                target_enterprise,
            )
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Audience room '{source_room.name}' (ID: {audience_room_id}) "
                    f"has already been copied to enterprise '{target_enterprise}'. "
                    "This room already exists in the target enterprise."
                ),
            )
        
        logger.info(
            "Audience room %s does not exist in target enterprise=%s, proceeding with copy",
            audience_room_id,
            target_enterprise,
        )

        # ---------------------------------------------------------------------
        # Step 2: Resolve source audience path
        # ---------------------------------------------------------------------
        source_audience_path = get_source_audience_path(source_room.source)
        logger.info("Source audience path: %s", source_audience_path)

        # ---------------------------------------------------------------------
        # Step 3: Get userId from target enterprise
        # ---------------------------------------------------------------------
        if payload.targetUserId:
            target_user_id = payload.targetUserId
            logger.info("Using provided target userId: %s", target_user_id)
        else:
            logger.info(
                "Fetching userId from target enterprise=%s",
                target_enterprise,
            )
            target_user_id = database.get_user_id_from_enterprise(
                enterprise_name=target_enterprise
            )
            if not target_user_id:
                raise HTTPException(
                    status_code=404,
                    detail=(
                        f"No userId found in target enterprise '{target_enterprise}'. "
                        "Ensure at least one AudienceRoom exists or provide targetUserId."
                    ),
                )
            logger.info("Target userId resolved from database: %s", target_user_id)
        # ---------------------------------------------------------------------
        # Step 4: List source S3 objects
        # ---------------------------------------------------------------------
        source_prefix = (
            f"{source_enterprise}/{source_audience_path}/{audience_room_id}/"
        )
        logger.info("Listing S3 objects with prefix: %s", source_prefix)

        source_s3_keys = list_s3_objects_with_prefix(source_prefix)

        if not source_s3_keys:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"No S3 files found for audience room {audience_room_id} "
                    f"in source enterprise '{source_enterprise}'"
                ),
            )

        logger.info("Found %d S3 objects to copy", len(source_s3_keys))

        # ---------------------------------------------------------------------
        # Step 5: Copy S3 objects to target enterprise
        # ---------------------------------------------------------------------
        copied_files = []
        failed_files = []

        target_prefix = (
            f"{target_enterprise}/{source_audience_path}/{audience_room_id}/"
        )
        logger.info("Copying S3 objects to prefix: %s", target_prefix)

        for source_key in source_s3_keys:
            try:
                destination_key = source_key.replace(
                    f"{source_enterprise}/{source_audience_path}/",
                    f"{target_enterprise}/{source_audience_path}/",
                )

                copy_s3_object(source_key, destination_key)

                copied_files.append(
                    {
                        "source": source_key,
                        "destination": destination_key,
                        "status": "success",
                    }
                )
            except Exception as e:
                logger.error(
                    "Failed to copy S3 object %s: %s",
                    source_key,
                    e,
                    exc_info=True,
                )
                failed_files.append(
                    {
                        "source": source_key,
                        "error": str(e),
                        "status": "failed",
                    }
                )

        logger.info(
            "S3 copy complete: %d succeeded, %d failed",
            len(copied_files),
            len(failed_files),
        )

        # ---------------------------------------------------------------------
        # Step 6: Upsert audience room in target DB
        # ---------------------------------------------------------------------
        updated_description_url = (
            replace_enterprise_in_s3_url(
                source_room.descriptionS3Url,
                source_enterprise,
                target_enterprise,
            )
            if source_room.descriptionS3Url
            else None
        )

        updated_indexes_url = (
            replace_enterprise_in_s3_url(
                source_room.indexesS3Url,
                source_enterprise,
                target_enterprise,
            )
            if source_room.indexesS3Url
            else None
        )

        target_room = database.upsert_audience_room(
            room_id=source_room.id,
            name=source_room.name,
            description_s3_url=updated_description_url,
            user_id=target_user_id,
            source=source_room.source,
            query=source_room.query,
            indexes_s3_url=updated_indexes_url,
            enterprise_name=target_enterprise,
        )

        logger.info(
            "Upserted audience room %s in target enterprise",
            target_room.id,
        )

        # ---------------------------------------------------------------------
        # Step 7: Upsert profiles
        # ---------------------------------------------------------------------
        upserted_profiles = []
        failed_profiles = []

        logger.info(
            "Upserting %d profiles in target database",
            len(source_room.profiles),
        )

        for profile in source_room.profiles:
            try:
                updated_profile_desc_url = (
                    replace_enterprise_in_s3_url(
                        profile.profileDescriptionS3Url,
                        source_enterprise,
                        target_enterprise,
                    )
                    if profile.profileDescriptionS3Url
                    else None
                )

                updated_posts_url = (
                    replace_enterprise_in_s3_url(
                        profile.postsS3Url,
                        source_enterprise,
                        target_enterprise,
                    )
                    if profile.postsS3Url
                    else None
                )

                updated_comments_url = (
                    replace_enterprise_in_s3_url(
                        profile.commentsS3Url,
                        source_enterprise,
                        target_enterprise,
                    )
                    if profile.commentsS3Url
                    else None
                )

                target_profile = database.upsert_audience_profile(
                    profile_id=profile.id,
                    audience_room_id=profile.audienceRoomId,
                    profile_name=profile.profileName,
                    profile_url=profile.profileUrl,
                    profile_description_s3_url=updated_profile_desc_url,
                    posts_s3_url=updated_posts_url,
                    comments_s3_url=updated_comments_url,
                    source=profile.source,
                    enterprise_name=target_enterprise,
                )

                upserted_profiles.append(
                    {
                        "profile_id": target_profile.id,
                        "profile_name": target_profile.profileName,
                        "status": "success",
                    }
                )
            except Exception as e:
                logger.error(
                    "Failed to upsert profile %s: %s",
                    profile.id,
                    e,
                    exc_info=True,
                )
                failed_profiles.append(
                    {
                        "profile_id": profile.id,
                        "profile_name": profile.profileName,
                        "error": str(e),
                        "status": "failed",
                    }
                )

        logger.info(
            "Profile upsert complete: %d succeeded, %d failed",
            len(upserted_profiles),
            len(failed_profiles),
        )
        # ---------------------------------------------------------------------
        # Step 8: Copy preview record to target enterprise
        # ---------------------------------------------------------------------
        logger.info(
            "Fetching preview record for room %s from source enterprise=%s",
            audience_room_id,
            source_enterprise,
        )
        
        source_preview = database.find_preview_by_room_id(
            room_id=audience_room_id,
            enterprise_name=source_enterprise
        )
        
        preview_copied = False
        if source_preview:
            logger.info(
                "Found preview record: room_id=%s, name=%s",
                source_preview.get('room_id'),
                source_preview.get('name'),
            )
            
            target_preview = database.upsert_preview(
                room_id=audience_room_id,  
                name=source_preview.get('name'),
                user_id=target_user_id, 
                description_summary=source_preview.get('description_summary'),
                source=source_preview.get('source'),
                total_profile_count=source_preview.get('total_profile_count', 0),
                profiles=source_preview.get('profiles'),
                enterprise_name=target_enterprise,
            )
            
            preview_copied = True
            logger.info(
                "Upserted preview record in target enterprise: room_id=%s, user_id=%s",
                target_preview.get('room_id'),
                target_preview.get('user_id'),
            )
        else:
            logger.warning(
                "No preview record found for room %s in source enterprise=%s",
                audience_room_id,
                source_enterprise,
            )

        # ---------------------------------------------------------------------
        # Step 9: Replicate DynamoDB Clones table entry
        # ---------------------------------------------------------------------
        dynamodb_replication_status = None
        if source_user_id and dynamodb_resource:
            try:
                logger.info(
                    "Replicating DynamoDB entry: source_user_id=%s, target_user_id=%s, clone_id=%s",
                    source_user_id,
                    target_user_id,
                    audience_room_id,
                )
                
                table = dynamodb_resource.Table("Clones")
                
                # Get the source item from DynamoDB
                try:
                    source_item = table.get_item(
                        Key={
                            'user_id': source_user_id,
                            'clone_id': audience_room_id
                        }
                    )
                    
                    if 'Item' not in source_item:
                        logger.warning(
                            "DynamoDB entry not found for user_id=%s, clone_id=%s. Skipping replication.",
                            source_user_id,
                            audience_room_id
                        )
                        dynamodb_replication_status = {
                            "status": "skipped",
                            "reason": "Source entry not found in DynamoDB"
                        }
                    else:
                        # Create replica with new user_id
                        replica_item = copy.deepcopy(source_item['Item'])
                        replica_item['user_id'] = target_user_id
                        
                        # Update updated_at timestamp to current time
                        current_time = datetime.now(timezone.utc).isoformat()
                        replica_item['updated_at'] = current_time
                        
                        # Put the replica item
                        table.put_item(Item=replica_item)
                        
                        logger.info(
                            "Successfully replicated DynamoDB entry: user_id=%s, clone_id=%s",
                            target_user_id,
                            audience_room_id
                        )
                        dynamodb_replication_status = {
                            "status": "success",
                            "source_user_id": source_user_id,
                            "target_user_id": target_user_id,
                            "clone_id": audience_room_id
                        }
                        
                except Exception as e:
                    logger.error(
                        "Failed to replicate DynamoDB entry: %s",
                        e,
                        exc_info=True
                    )
                    dynamodb_replication_status = {
                        "status": "failed",
                        "error": str(e)
                    }
                    
            except Exception as e:
                logger.error(
                    "Error accessing DynamoDB: %s",
                    e,
                    exc_info=True
                )
                dynamodb_replication_status = {
                    "status": "error",
                    "error": f"DynamoDB access error: {str(e)}"
                }
        else:
            if not source_user_id:
                logger.warning("Skipping DynamoDB replication: source_user_id not available")
            if not dynamodb_resource:
                logger.warning("Skipping DynamoDB replication: DynamoDB resource not initialized")
            dynamodb_replication_status = {
                "status": "skipped",
                "reason": "source_user_id or dynamodb_resource not available"
            }

        logger.info("=== COPY AUDIENCE ROOM TO CLIENT REQUEST SUCCESS ===")
        return {
            "status": "success",
            "message": (
                f"Successfully copied audience room {audience_room_id} "
                f"from '{source_enterprise}' to '{target_enterprise}'"
            ),
            "audience_room_id": audience_room_id,
            "audience_room_name": source_room.name,
            "source_enterprise": source_enterprise,
            "target_enterprise": target_enterprise,
            "s3_files": {
                "total": len(source_s3_keys),
                "copied": len(copied_files),
                "failed": len(failed_files),
                "copied_files": copied_files,
                "failed_files": failed_files,
            },
            "database_records": {
                "room_upserted": True,
                "profiles_total": len(source_room.profiles),
                "profiles_upserted": len(upserted_profiles),
                "profiles_failed": len(failed_profiles),
                "upserted_profiles": upserted_profiles,
                "failed_profiles": failed_profiles,
            },
            "dynamodb_replication": dynamodb_replication_status,
            "preview": {
                "copied": preview_copied,
                "room_id": audience_room_id,
                "target_user_id": target_user_id,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("=== ERROR in copy_audience_room_to_client ===")
        logger.error(
            "Error copying audience room %s: %s",
            audience_room_id,
            e,
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to copy audience room: {str(e)}",
        )

