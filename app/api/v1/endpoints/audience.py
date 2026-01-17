"""Audience room endpoints."""
import uuid
import asyncio
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, HTTPException, Path, Query
from app.models.schemas import CreateAudienceRoomRequest, UpdateAudienceRoomNameRequest
from app.config import s3_client, s3_bucket, logger
from app.utils.helpers import ensure_db_available
from app.utils.s3_utils import upload_json_to_s3, extract_s3_key_from_url, fetch_json_from_s3, get_s3_key_for_audience
from app.services.summary_service import process_profile_summary
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
    ensure_db_available("audience")
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")

    room_id = str(uuid.uuid4())

    # Upload audience description to S3
    description_key = get_s3_key_for_audience(room_id, "description.json", payload.enterpriseName)
    description_url = upload_json_to_s3(
        description_key,
        {
            "audience_room_id": room_id,
            "audience_room_name": payload.audience_room_name,
            "description": payload.audience_description,
        },
    )

    # Handle search results/indexes if provided
    indexes_s3_url = None
    if payload.query and payload.search_results:
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
            indexes_key = get_s3_key_for_audience(room_id, "indexes.json", payload.enterpriseName)
            indexes_s3_url = upload_json_to_s3(indexes_key, indexes_data)
            logger.info(f"Uploaded search results/indexes to S3 for audience room {room_id}")
        except Exception as e:
            logger.error(f"Failed to upload search results to S3: {e}")
            # Continue with room creation even if indexes upload fails

    # Build profile records and upload payloads to S3 (summary starts as null)
    profile_creates = []
    for profile in payload.profiles:
        profile_id = str(uuid.uuid4())
        profile_key = get_s3_key_for_audience(room_id, f"profiles/{profile_id}/profile.json", payload.enterpriseName)
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
            "summary": None,
        }
        profile_url = upload_json_to_s3(profile_key, profile_payload)

        profile_creates.append(
            {
                "id": profile_id,
                "profileName": profile.name,
                "profileUrl": profile.linkedin_profile_url,
                "profileDescriptionS3Url": profile_url,
                "postsS3Url": None,
            }
        )

    try:
        # Log enterprise name for debugging
        logger.info(f"Creating audience room with enterpriseName: {payload.enterpriseName}")
        
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

        return {
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
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating audience room: {e}")
        raise HTTPException(status_code=500, detail="Failed to create audience room")


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
    ensure_db_available("audience")
    
    try:
        updated_room = database.update_audience_room(
            room_id=request.audienceRoomId,
            data={"name": request.newName},
            enterprise_name=request.enterpriseName
        )
        
        if not updated_room:
            raise HTTPException(
                status_code=404,
                detail=f"Audience room {request.audienceRoomId} not found"
            )
        
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
        logger.error(f"Error updating audience room name for room {request.audienceRoomId}: {e}")
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
    
    This endpoint:
    1. Deletes all S3 files associated with the audience room
    2. Deletes all profiles from the AudienceProfile table (cascade delete)
    3. Deletes the audience room from the AudienceRoom table
    
    Args:
        audience_room_id: The audience room ID
        enterpriseName: Optional enterprise name (gamma, app, entelligence, beta). 
                       If not provided, uses AUDIENCE_DATABASE_URL.
    """
    ensure_db_available("audience")
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")

    try:
        # Fetch audience room with all profiles using enterprise-specific database if provided
        audience_room = database.find_audience_room_by_id(audience_room_id, include_profiles=True, enterprise_name=enterpriseName)
        
        if not audience_room:
            raise HTTPException(status_code=404, detail=f"Audience room {audience_room_id} not found")
        
        profiles = audience_room.profiles
        profile_count = len(profiles)
        
        # Delete all S3 files associated with this audience room
        # Use the enterprise-based prefix
        normalized_enterprise = enterpriseName.lower().strip() if enterpriseName else "default"
        s3_prefix = f"linkedin-audience/{normalized_enterprise}/{audience_room_id}/"
        deleted_s3_files = []
        
        try:
            # List all objects with the prefix
            paginator = s3_client.get_paginator('list_objects_v2')
            pages = paginator.paginate(Bucket=s3_bucket, Prefix=s3_prefix)
            
            # Collect all object keys to delete
            objects_to_delete = []
            for page in pages:
                if 'Contents' in page:
                    for obj in page['Contents']:
                        objects_to_delete.append({'Key': obj['Key']})
                        deleted_s3_files.append(obj['Key'])
            
            # Delete all objects in batch (max 1000 objects per request)
            if objects_to_delete:
                for i in range(0, len(objects_to_delete), 1000):
                    batch = objects_to_delete[i:i+1000]
                    s3_client.delete_objects(
                        Bucket=s3_bucket,
                        Delete={
                            'Objects': batch,
                            'Quiet': True
                        }
                    )
                logger.info(f"Deleted {len(objects_to_delete)} S3 objects for audience room {audience_room_id}")
            else:
                logger.warning(f"No S3 objects found for audience room {audience_room_id}")
                
        except Exception as e:
            logger.error(f"Error deleting S3 files for audience room {audience_room_id}: {e}")
            # Continue with database deletion even if S3 deletion fails
        
        # Delete all profiles from database first using enterprise-specific database if provided
        if profiles:
            try:
                deleted_count = database.delete_audience_profiles_by_room(audience_room_id, enterprise_name=enterpriseName)
                logger.info(f"Deleted {deleted_count} profiles for audience room {audience_room_id}")
            except Exception as e:
                logger.error(f"Error deleting profiles for audience room {audience_room_id}: {e}")
                raise HTTPException(status_code=500, detail=f"Failed to delete profiles: {str(e)}")
        
        # Delete the audience room from database using enterprise-specific database if provided
        try:
            database.delete_audience_room(audience_room_id, enterprise_name=enterpriseName)
            logger.info(f"Deleted audience room {audience_room_id}")
        except Exception as e:
            logger.error(f"Error deleting audience room {audience_room_id}: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to delete audience room: {str(e)}")
        
        return {
            "message": f"Successfully deleted audience room {audience_room_id}",
            "audience_room_id": audience_room_id,
            "audience_room_name": audience_room.name,
            "profiles_deleted": profile_count,
            "s3_files_deleted": len(deleted_s3_files),
            "s3_files": deleted_s3_files
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting audience room {audience_room_id}: {e}")
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
    ensure_db_available("audience")
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")
    
    try:
        # Verify room exists using enterprise-specific database if provided
        room = database.find_audience_room_by_id(audience_room_id, enterprise_name=enterpriseName)
        if not room:
            raise HTTPException(status_code=404, detail=f"Audience room {audience_room_id} not found")
        
        if not room.descriptionS3Url:
            raise HTTPException(status_code=404, detail="Description not found for this audience room")
        
        # Extract S3 key from URL
        description_key = extract_s3_key_from_url(room.descriptionS3Url)
        if not description_key:
            raise HTTPException(status_code=500, detail="Invalid S3 URL format")
        
        # Fetch JSON from S3
        description_data = fetch_json_from_s3(description_key)
        return description_data
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching description for audience room {audience_room_id}: {e}")
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
    ensure_db_available("audience")
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")
    
    try:
        # Verify profile exists and belongs to the room using enterprise-specific database if provided
        profile = database.find_audience_profile_by_id(profile_id, include_room=True, enterprise_name=enterpriseName)
        if not profile:
            raise HTTPException(status_code=404, detail=f"Profile {profile_id} not found")
        
        if profile.audienceRoomId != audience_room_id:
            raise HTTPException(status_code=404, detail=f"Profile {profile_id} does not belong to audience room {audience_room_id}")
        
        if not profile.profileDescriptionS3Url:
            raise HTTPException(status_code=404, detail="Profile description not found")
        
        # Extract S3 key from URL
        profile_key = extract_s3_key_from_url(profile.profileDescriptionS3Url)
        if not profile_key:
            raise HTTPException(status_code=500, detail="Invalid S3 URL format")
        
        # Fetch JSON from S3
        profile_data = fetch_json_from_s3(profile_key)
        return profile_data
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching profile description for {profile_id}: {e}")
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
    ensure_db_available("audience")
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")
    
    try:
        # Verify profile exists and belongs to the room using enterprise-specific database if provided
        profile = database.find_audience_profile_by_id(profile_id, include_room=True, enterprise_name=enterpriseName)
        if not profile:
            raise HTTPException(status_code=404, detail=f"Profile {profile_id} not found")
        
        if profile.audienceRoomId != audience_room_id:
            raise HTTPException(status_code=404, detail=f"Profile {profile_id} does not belong to audience room {audience_room_id}")
        
        if not profile.postsS3Url:
            raise HTTPException(status_code=404, detail="Posts not found for this profile")
        
        # Extract S3 key from URL
        posts_key = extract_s3_key_from_url(profile.postsS3Url)
        if not posts_key:
            raise HTTPException(status_code=500, detail="Invalid S3 URL format")
        
        # Fetch JSON from S3
        posts_data = fetch_json_from_s3(posts_key)
        return posts_data
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching posts for profile {profile_id}: {e}")
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
    ensure_db_available("audience")
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")
    
    try:
        # Verify room exists using enterprise-specific database if provided
        room = database.find_audience_room_by_id(audience_room_id, enterprise_name=enterpriseName)
        if not room:
            raise HTTPException(status_code=404, detail=f"Audience room {audience_room_id} not found")
        
        if not room.indexesS3Url:
            raise HTTPException(status_code=404, detail="Indexes not found for this audience room")
        
        # Extract S3 key from URL
        indexes_key = extract_s3_key_from_url(room.indexesS3Url)
        if not indexes_key:
            raise HTTPException(status_code=500, detail="Invalid S3 URL format")
        
        # Fetch JSON from S3
        indexes_data = fetch_json_from_s3(indexes_key)
        return indexes_data
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching indexes for audience room {audience_room_id}: {e}")
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
    ensure_db_available("audience")
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")
    
    try:
        # Verify profile exists and belongs to the room using enterprise-specific database if provided
        profile = database.find_audience_profile_by_id(profile_id, include_room=True, enterprise_name=enterpriseName)
        if not profile:
            raise HTTPException(status_code=404, detail=f"Profile {profile_id} not found")
        
        if profile.audienceRoomId != audience_room_id:
            raise HTTPException(status_code=404, detail=f"Profile {profile_id} does not belong to audience room {audience_room_id}")
        
        if not profile.commentsS3Url:
            raise HTTPException(status_code=404, detail="Comments not found for this profile")
        
        # Extract S3 key from URL
        comments_key = extract_s3_key_from_url(profile.commentsS3Url)
        if not comments_key:
            raise HTTPException(status_code=500, detail="Invalid S3 URL format")
        
        # Fetch JSON from S3
        comments_data = fetch_json_from_s3(comments_key)
        return comments_data
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching comments for profile {profile_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch comments")


@router.post("/api/v1/audience-rooms/{audience_room_id}/generate-summaries")
async def generate_profile_summaries(
    audience_room_id: str = Path(...),
    offset: int = Query(0, ge=0, description="Offset for chunking (start index)"),
    limit: int = Query(10, ge=1, le=20, description="Number of profiles to process per chunk (max 20)"),
    enterpriseName: Optional[str] = Query(None, description="Enterprise name (gamma, app, entelligence, beta). If not provided, uses default audience database.")
):
    """
    Generate summaries, keywords, and highlights for profiles in an audience room.
    
    Uses chunking to avoid timeouts - processes profiles in smaller batches.
    Client should call this endpoint multiple times with increasing offsets until has_more is false.
    
    Flow:
    1. Fetch all profiles in the audience room
    2. Process only the chunk specified by offset/limit
    3. For each profile in chunk:
       - Fetch posts JSON from S3
       - Generate summary, keywords, and highlights using OpenAI
       - Update profile description JSON in S3 with the new data
    4. Return results with info about remaining chunks
    
    Example usage:
    - First call: POST /generate-summaries?offset=0&limit=10
    - Second call: POST /generate-summaries?offset=10&limit=10
    - Continue until has_more is false
    
    Query Parameters:
    - enterpriseName (optional): Enterprise name to determine which database to use:
        - "gamma" -> uses GAMMA_DATABASE_URL
        - "app" -> uses APP_DATABASE_URL
        - "entelligence" -> uses ENTELLIGENCE_DATABASE_URL
        - "beta" -> uses BETA_DATABASE_URL
        - If not provided, uses AUDIENCE_DATABASE_URL
    """
    ensure_db_available("audience")
    from app.config import openai_client
    if not openai_client:
        raise HTTPException(status_code=503, detail="OpenAI client not initialized. Please set OPENAI_API_KEY.")
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")
    
    try:
        # Fetch audience room and profiles
        audience_room = database.find_audience_room_by_id(audience_room_id, include_profiles=True, enterprise_name=enterpriseName)
        if not audience_room:
            raise HTTPException(status_code=404, detail=f"Audience room {audience_room_id} not found")
        
        all_profiles = audience_room.profiles
        if not all_profiles:
            raise HTTPException(status_code=404, detail=f"No profiles found in audience room {audience_room_id}")
        
        total_profiles = len(all_profiles)
        
        # Calculate chunk boundaries
        chunk_start = offset
        chunk_end = min(offset + limit, total_profiles)
        profiles_chunk = all_profiles[chunk_start:chunk_end]
        
        if not profiles_chunk:
            return {
                "audience_room_id": audience_room_id,
                "total_profiles": total_profiles,
                "chunk": {
                    "offset": offset,
                    "limit": limit,
                    "processed": 0,
                    "start_index": chunk_start,
                    "end_index": chunk_end
                },
                "success_count": 0,
                "skipped_count": 0,
                "error_count": 0,
                "has_more": False,
                "next_offset": None,
                "profiles": []
            }
        
        logger.info(f"Processing chunk {chunk_start}-{chunk_end} of {total_profiles} profiles for audience room {audience_room_id}")
        
        # Rate-limited batching to avoid OpenAI rate limits
        MAX_CONCURRENT = 2
        semaphore = asyncio.Semaphore(MAX_CONCURRENT)
        
        async def rate_limited_process(profile):
            async with semaphore:
                result = await process_profile_summary(profile, audience_room_id, enterprise_name=enterpriseName)
                await asyncio.sleep(1.0)  # Delay to spread out API requests
                return result
        
        tasks = [rate_limited_process(profile) for profile in profiles_chunk]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Process results and handle exceptions
        processed_results = []
        success_count = 0
        error_count = 0
        skipped_count = 0
        
        for idx, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Error processing profile {profiles_chunk[idx].id}: {result}")
                processed_results.append({
                    "profile_id": profiles_chunk[idx].id,
                    "profile_name": profiles_chunk[idx].profileName,
                    "status": "error",
                    "reason": "exception",
                    "error": str(result)
                })
                error_count += 1
            else:
                processed_results.append(result)
                if result["status"] == "success":
                    success_count += 1
                elif result["status"] == "skipped":
                    skipped_count += 1
                else:
                    error_count += 1
        
        # Calculate if there are more chunks
        has_more = chunk_end < total_profiles
        next_offset = chunk_end if has_more else None
        
        return {
            "audience_room_id": audience_room_id,
            "total_profiles": total_profiles,
            "chunk": {
                "offset": offset,
                "limit": limit,
                "processed": len(profiles_chunk),
                "start_index": chunk_start,
                "end_index": chunk_end
            },
            "success_count": success_count,
            "skipped_count": skipped_count,
            "error_count": error_count,
            "has_more": has_more,
            "next_offset": next_offset,
            "profiles": processed_results
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating profile summaries: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to generate summaries: {str(e)}")


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
    from app.config import openai_client
    if not openai_client:
        raise HTTPException(status_code=503, detail="OpenAI client not initialized. Please set OPENAI_API_KEY.")
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
                detail="No profile summaries found. Please generate profile summaries first using /api/v1/audience-rooms/{audience_room_id}/generate-summaries"
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
        if "\n\n" in full_group_prompt:
            parts = full_group_prompt.split("\n\n", 1)
            system_message = parts[0] if parts[0].startswith("You are") else "You are an expert at analyzing groups of LinkedIn profiles and generating comprehensive, insightful high-level summaries. Write detailed, informative summaries that capture collective patterns and insights."
            user_prompt = parts[1] if len(parts) > 1 else full_group_prompt
        else:
            # Fallback if prompt doesn't have clear separation
            system_message = "You are an expert at analyzing groups of LinkedIn profiles and generating comprehensive, insightful high-level summaries. Write detailed, informative summaries that capture collective patterns and insights."
            user_prompt = full_group_prompt
        
        # Generate group summary using OpenAI
        try:
            completion = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": user_prompt}
                ],
                max_tokens=1200,
                temperature=0.3,
            )
            
            group_summary = completion.choices[0].message.content.strip()
            
            # Generate traits based on profile summaries using LangSmith prompt
            import json
            from prompts import traits_generation_prompt
            
            full_traits_prompt = traits_generation_prompt.format(
                total_profiles=len(profile_summaries),
                combined_summaries=combined_summaries
            )
            
            # Split the prompt into system and user messages
            if "\n\n" in full_traits_prompt:
                parts = full_traits_prompt.split("\n\n", 1)
                traits_system_message = parts[0] if parts[0].startswith("You are") else "You are an expert at analyzing professional profiles and generating structured trait data. Always return valid JSON only, no additional text."
                traits_prompt = parts[1] if len(parts) > 1 else full_traits_prompt
            else:
                # Fallback if prompt doesn't have clear separation
                traits_system_message = "You are an expert at analyzing professional profiles and generating structured trait data. Always return valid JSON only, no additional text."
                traits_prompt = full_traits_prompt
            
            try:
                traits_completion = openai_client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": traits_system_message},
                        {"role": "user", "content": traits_prompt}
                    ],
                    max_tokens=2000,
                    temperature=0.3,
                )
                
                traits_response = traits_completion.choices[0].message.content.strip()
                
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

