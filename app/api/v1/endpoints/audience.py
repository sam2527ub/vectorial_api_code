"""Audience room endpoints."""
import uuid
import copy
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, HTTPException, Path, Query
from app.models.schemas import CreateAudienceRoomRequest
from app.config import s3_client, s3_bucket, logger, dynamodb_resource
from app.utils.helpers import ensure_db_available
from app.utils.s3_utils import upload_json_to_s3, extract_s3_key_from_url, fetch_json_from_s3, get_s3_key_for_audience, ensure_enterprise_audience_folders_exist, get_source_audience_path, list_s3_objects_with_prefix, copy_s3_object, replace_enterprise_in_s3_url
from app.services.summary_service import process_profile_summary
from app.services.openai_service import call_claude_with_retry, split_prompt_into_messages
from app.services.dynamic_context_window_management_service import context_manager
from app import database
from app.database.enterprise_registry import get_all_enterprises

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
        
        # Use dynamic context window management for group summary
        group_model = "anthropic/claude-sonnet-4.5"
        max_completion_tokens = 1200
        
        adjusted_user_prompt, adjust_metadata = context_manager.adjust_content_to_fit_context_window(
            content=user_prompt,
            system_message=system_message,
            model_name=group_model,
            max_completion_tokens=max_completion_tokens
        )
        
        if adjust_metadata.get("truncated"):
            logger.warning(
                f"Audience room {audience_room_id}: Group summary prompt truncated "
                f"({adjust_metadata.get('truncation_ratio', 0):.1%} reduction) "
                f"to fit {group_model} context window"
            )
        
        # Generate group summary using Claude Sonnet
        try:
            group_summary = await call_claude_with_retry(
                context_id=audience_room_id,
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": adjusted_user_prompt}
                ],
                max_tokens=max_completion_tokens,
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
            
            # Use dynamic context window management for traits generation
            traits_max_completion_tokens = 2000
            adjusted_traits_prompt, traits_adjust_metadata = context_manager.adjust_content_to_fit_context_window(
                content=traits_prompt,
                system_message=traits_system_message,
                model_name=group_model,
                max_completion_tokens=traits_max_completion_tokens
            )
            
            if traits_adjust_metadata.get("truncated"):
                logger.warning(
                    f"Audience room {audience_room_id}: Traits prompt truncated "
                    f"({traits_adjust_metadata.get('truncation_ratio', 0):.1%} reduction) "
                    f"to fit {group_model} context window"
                )
            
            try:
                # Generate traits using Claude Sonnet
                traits_response = await call_claude_with_retry(
                    context_id=audience_room_id,
                    messages=[
                        {"role": "system", "content": traits_system_message},
                        {"role": "user", "content": adjusted_traits_prompt}
                    ],
                    max_tokens=traits_max_completion_tokens,
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