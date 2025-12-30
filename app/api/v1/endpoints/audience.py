"""Audience room endpoints."""
import uuid
import asyncio
from fastapi import APIRouter, HTTPException, Path
from app.models.schemas import CreateAudienceRoomRequest
from app.config import s3_client, s3_bucket, logger
from app.utils.helpers import ensure_db_available
from app.utils.s3_utils import upload_json_to_s3, extract_s3_key_from_url, fetch_json_from_s3
from app.services.summary_service import process_profile_summary
from app import database

router = APIRouter()


@router.post("/api/v1/audience-rooms")
async def create_audience_room(payload: CreateAudienceRoomRequest):
    """
    Create an audience room, store its description and profile payloads in S3, and persist metadata in Postgres.
    - Stores audience description at: audiences/{audience_room_id}/description.json
    - Stores each profile payload (with summary=null) at: audiences/{audience_room_id}/profiles/{profile_id}/profile.json
    """
    ensure_db_available("audience")
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")

    room_id = str(uuid.uuid4())

    # Upload audience description to S3
    description_key = f"audiences/{room_id}/description.json"
    description_url = upload_json_to_s3(
        description_key,
        {
            "audience_room_id": room_id,
            "audience_room_name": payload.audience_room_name,
            "description": payload.audience_description,
        },
    )

    # Build profile records and upload payloads to S3 (summary starts as null)
    profile_creates = []
    for profile in payload.profiles:
        profile_id = str(uuid.uuid4())
        profile_key = f"audiences/{room_id}/profiles/{profile_id}/profile.json"
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
                "linkedinUrl": profile.linkedin_profile_url,
                "profileDescriptionS3Url": profile_url,
                "postsS3Url": None,
            }
        )

    try:
        room = database.create_audience_room(
            room_id=room_id,
            name=payload.audience_room_name,
            description_s3_url=description_url,
            user_id=payload.userId,
            profiles_data=profile_creates,
        )

        return {
            "audience_room_id": room.id,
            "audience_room_name": room.name,
            "description_s3_url": room.descriptionS3Url,
            "userId": room.userId,
            "profiles_created": len(room.profiles),
            "profiles": [
                {
                    "profile_id": p.id,
                    "profile_name": p.profileName,
                    "linkedin_url": p.linkedinUrl,
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


@router.delete("/api/v1/audience-rooms/{audience_room_id}")
async def delete_audience_room(audience_room_id: str = Path(...)):
    """
    Delete an audience room and all associated data.
    
    This endpoint:
    1. Deletes all S3 files associated with the audience room
    2. Deletes all profiles from the AudienceProfile table (cascade delete)
    3. Deletes the audience room from the AudienceRoom table
    """
    ensure_db_available("audience")
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")

    try:
        # Fetch audience room with all profiles
        audience_room = database.find_audience_room_by_id(audience_room_id, include_profiles=True)
        
        if not audience_room:
            raise HTTPException(status_code=404, detail=f"Audience room {audience_room_id} not found")
        
        profiles = audience_room.profiles
        profile_count = len(profiles)
        
        # Delete all S3 files associated with this audience room
        s3_prefix = f"audiences/{audience_room_id}/"
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
        
        # Delete all profiles from database first
        if profiles:
            try:
                deleted_count = database.delete_audience_profiles_by_room(audience_room_id)
                logger.info(f"Deleted {deleted_count} profiles for audience room {audience_room_id}")
            except Exception as e:
                logger.error(f"Error deleting profiles for audience room {audience_room_id}: {e}")
                raise HTTPException(status_code=500, detail=f"Failed to delete profiles: {str(e)}")
        
        # Delete the audience room from database
        try:
            database.delete_audience_room(audience_room_id)
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
async def get_audience_room_description(audience_room_id: str = Path(...)):
    """Fetch and return the audience room description JSON from S3."""
    ensure_db_available("audience")
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")
    
    try:
        # Verify room exists
        room = database.find_audience_room_by_id(audience_room_id)
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
    profile_id: str = Path(...)
):
    """Fetch and return the profile description JSON from S3."""
    ensure_db_available("audience")
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")
    
    try:
        # Verify profile exists and belongs to the room
        profile = database.find_audience_profile_by_id(profile_id, include_room=True)
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
    profile_id: str = Path(...)
):
    """Fetch and return the profile posts JSON from S3."""
    ensure_db_available("audience")
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")
    
    try:
        # Verify profile exists and belongs to the room
        profile = database.find_audience_profile_by_id(profile_id, include_room=True)
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


@router.post("/api/v1/audience-rooms/{audience_room_id}/generate-summaries")
async def generate_profile_summaries(audience_room_id: str = Path(...)):
    """
    Generate summaries, keywords, and highlights for all profiles in an audience room.
    
    Flow:
    1. Fetch all profiles in the audience room
    2. For each profile:
       - Fetch posts JSON from S3
       - Generate summary, keywords, and highlights using OpenAI
       - Update profile description JSON in S3 with the new data
    3. Process profiles in parallel to avoid timeouts
    """
    ensure_db_available("audience")
    from app.config import openai_client
    if not openai_client:
        raise HTTPException(status_code=503, detail="OpenAI client not initialized. Please set OPENAI_API_KEY.")
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")
    
    try:
        # Fetch audience room and profiles
        audience_room = database.find_audience_room_by_id(audience_room_id, include_profiles=True)
        if not audience_room:
            raise HTTPException(status_code=404, detail=f"Audience room {audience_room_id} not found")
        
        profiles = audience_room.profiles
        if not profiles:
            raise HTTPException(status_code=404, detail=f"No profiles found in audience room {audience_room_id}")
        
        logger.info(f"Generating summaries for {len(profiles)} profiles in audience room {audience_room_id}")
        
        # Rate-limited batching to avoid OpenAI rate limits
        MAX_CONCURRENT = 2
        semaphore = asyncio.Semaphore(MAX_CONCURRENT)
        
        async def rate_limited_process(profile):
            async with semaphore:
                result = await process_profile_summary(profile, audience_room_id)
                await asyncio.sleep(1.0)  # Delay to spread out API requests
                return result
        
        tasks = [rate_limited_process(profile) for profile in profiles]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Process results and handle exceptions
        processed_results = []
        success_count = 0
        error_count = 0
        skipped_count = 0
        
        for idx, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Error processing profile {profiles[idx].id}: {result}")
                processed_results.append({
                    "profile_id": profiles[idx].id,
                    "profile_name": profiles[idx].profileName,
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
        
        return {
            "audience_room_id": audience_room_id,
            "total_profiles": len(profiles),
            "success_count": success_count,
            "skipped_count": skipped_count,
            "error_count": error_count,
            "profiles": processed_results
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating profile summaries: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to generate summaries: {str(e)}")


@router.post("/api/v1/audience-rooms/{audience_room_id}/generate-group-summary")
async def generate_group_summary(audience_room_id: str = Path(...)):
    """
    Generate a group summary and traits for an audience room based on all profile summaries.
    
    Flow:
    1. Fetch all profiles in the audience room
    2. For each profile, fetch description JSON from S3 and extract the summary
    3. Combine all profile summaries
    4. Generate a group summary using OpenAI based on the combined summaries
    5. Generate traits (5 traits with keywordTags and descriptions) based on profile summaries
    6. Update the audience room description JSON in S3 with both summary and traits fields
    """
    ensure_db_available("audience")
    from app.config import openai_client
    if not openai_client:
        raise HTTPException(status_code=503, detail="OpenAI client not initialized. Please set OPENAI_API_KEY.")
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")
    
    try:
        # Fetch audience room and profiles
        audience_room = database.find_audience_room_by_id(audience_room_id, include_profiles=True)
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
        
        # Build the prompt according to the template
        user_prompt = f"""Analyze the following group of {len(profile_summaries)} profiles who work at {company_type}.

Companies represented: {company_list}

Individual Profile Summaries:
{combined_summaries}

Generate a comprehensive high-level summary (6-10 sentences) that covers:
1. Overall themes and patterns across all profiles in this group
2. Common topics, technologies, or expertise areas shared among them
3. Company culture and stage characteristics evident from their posts
4. Professional focus areas (e.g., technical depth, thought leadership, product development)
5. Industry trends or insights that emerge from the collective content
6. Unique characteristics or differentiators of this group
7. Common posting styles or engagement patterns
8. Key value propositions or strengths evident across the group

Write in a natural, engaging way that provides insights into this collective group of professionals from {company_type}.

Respond with ONLY the summary text, no JSON or formatting."""
        
        system_message = "You are an expert at analyzing groups of LinkedIn profiles and generating comprehensive, insightful high-level summaries. Write detailed, informative summaries that capture collective patterns and insights."
        
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
            
            # Generate traits based on profile summaries
            import json
            traits_prompt = f"""Analyze the following group of {len(profile_summaries)} profiles and generate traits in JSON format.

Individual Profile Summaries:
{combined_summaries}

Based on these profiles, generate a traits JSON object with exactly 5 traits. Each trait must have:
- title: One of these exact titles (keep them as-is):
  1. "Skills & Expertise"
  2. "Working Style"
  3. "Motivations & Values"
  4. "Pain Points & Needs"
  5. "Organizational Leadership & Psychographic Profile"

- keywordTags: An array of 4-6 specific keyword tags relevant to this group of profiles
- descriptions: An array of 4-6 descriptive sentences (one per keywordTag) that explain how these tags apply to this specific group

Return ONLY valid JSON in this exact format:
{{
  "traits": [
    {{
      "title": "Skills & Expertise",
      "keywordTags": ["tag1", "tag2", "tag3", "tag4", "tag5"],
      "descriptions": ["description1", "description2", "description3", "description4", "description5"]
    }},
    ...
  ]
}}

Make sure the JSON is valid and properly formatted. Do not include any text before or after the JSON."""
            
            traits_system_message = "You are an expert at analyzing professional profiles and generating structured trait data. Always return valid JSON only, no additional text."
            
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
            database.update_audience_room(audience_room_id, {"descriptionS3Url": updated_description_url})
            
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
async def remove_labels_from_posts(audience_room_id: str = Path(...)):
    """
    Remove the 'labels' field from all posts JSON for all profiles in an audience room.
    
    Flow:
    1. Fetch all profiles in the audience room
    2. For each profile with postsS3Url:
       - Fetch posts JSON from S3
       - Remove 'labels' field from each post
       - Upload updated JSON back to S3
       - Update the profile record in the database
    """
    ensure_db_available("audience")
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")
    
    try:
        # Fetch audience room and profiles
        audience_room = database.find_audience_room_by_id(audience_room_id, include_profiles=True)
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
                database.update_audience_profile(profile_id, {"postsS3Url": updated_posts_url})
                
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

