"""Classifier endpoints."""
import json
from fastapi import APIRouter, HTTPException
from app.models.schemas import RunClassifierRequest, RunClassifierForProfilesRequest
from app.config import groq_client, s3_client, s3_bucket, logger
from app.utils.helpers import ensure_db_available
from app.utils.s3_utils import extract_s3_key_from_url, fetch_json_from_s3, upload_json_to_s3
from app.services.classifier_service import classify_posts_batch
from app import database

router = APIRouter()


@router.post("/api/classifier/run")
async def run_classifier(payload: RunClassifierRequest):
    """
    Run a classifier on all posts in an audience room.
    
    Flow:
    1. Fetch Classifier details from audience database
    2. Fetch AudienceRoom and all associated Profiles
    3. For each Profile, download posts from S3
    4. Classify each post using Groq LLM
    5. Add labels to posts and upload back to S3
    """
    ensure_db_available("audience")
    if not groq_client:
        raise HTTPException(status_code=503, detail="Groq client not initialized. Please set GROQ_API_KEY.")
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")
    
    try:
        # 1. Fetch Classifier details
        classifier = database.find_post_classifier_by_id(payload.classifierId)
        if not classifier:
            raise HTTPException(status_code=404, detail=f"Classifier {payload.classifierId} not found")
        
        # Extract classifier fields
        classifier_name = classifier.name
        classifier_prompt = classifier.prompt or ""
        classifier_description = classifier.description or ""
        
        # Handle labels (JSON field - could be list, dict, or string representation)
        classifier_labels = []
        try:
            labels_raw = classifier.labels
            if isinstance(labels_raw, list):
                classifier_labels = labels_raw
            elif isinstance(labels_raw, str):
                try:
                    parsed = json.loads(labels_raw)
                    classifier_labels = parsed if isinstance(parsed, list) else [labels_raw]
                except (json.JSONDecodeError, TypeError):
                    classifier_labels = [labels_raw]
            elif isinstance(labels_raw, dict):
                if "labels" in labels_raw and isinstance(labels_raw["labels"], list):
                    classifier_labels = labels_raw["labels"]
                else:
                    classifier_labels = list(labels_raw.keys()) if labels_raw else []
            else:
                try:
                    classifier_labels = list(labels_raw) if labels_raw else []
                except (TypeError, ValueError):
                    classifier_labels = []
        except Exception as e:
            logger.warning(f"Error parsing classifier labels: {e}")
            classifier_labels = []
        
        # Ensure all labels are strings
        classifier_labels = [str(label) for label in classifier_labels if label]
        
        if not classifier_labels:
            raise HTTPException(status_code=400, detail="Classifier has no labels defined")
        
        # Handle examples (JSON field)
        classifier_examples = None
        if classifier.examples:
            try:
                examples_raw = classifier.examples
                if isinstance(examples_raw, dict):
                    classifier_examples = examples_raw
                elif isinstance(examples_raw, str):
                    try:
                        classifier_examples = json.loads(examples_raw)
                    except json.JSONDecodeError:
                        classifier_examples = None
                elif isinstance(examples_raw, list):
                    classifier_examples = examples_raw
            except Exception as e:
                logger.warning(f"Error parsing classifier examples: {e}")
                classifier_examples = None
        
        # 2. Fetch AudienceRoom and Profiles
        audience_room = database.find_audience_room_by_id(payload.audienceRoomId, include_profiles=True)
        if not audience_room:
            raise HTTPException(status_code=404, detail=f"Audience room {payload.audienceRoomId} not found")
        
        profiles = audience_room.profiles
        if not profiles:
            raise HTTPException(status_code=404, detail=f"No profiles found in audience room {payload.audienceRoomId}")
        
        # 3. Process each profile's posts
        processed_profiles = []
        total_posts_classified = 0
        
        for profile in profiles:
            profile_id = profile.id
            profile_name = profile.profileName
            
            # Skip if no posts URL
            if not profile.postsS3Url:
                logger.warning(f"Profile {profile_id} ({profile_name}) has no posts URL, skipping")
                processed_profiles.append({
                    "profile_id": profile_id,
                    "profile_name": profile_name,
                    "status": "skipped",
                    "reason": "no_posts_url",
                    "posts_classified": 0
                })
                continue
            
            try:
                # Extract S3 key and fetch posts
                posts_key = extract_s3_key_from_url(profile.postsS3Url)
                if not posts_key:
                    logger.error(f"Invalid S3 URL format for profile {profile_id}: {profile.postsS3Url}")
                    processed_profiles.append({
                        "profile_id": profile_id,
                        "profile_name": profile_name,
                        "status": "error",
                        "reason": "invalid_s3_url",
                        "posts_classified": 0
                    })
                    continue
                
                # Fetch posts JSON from S3
                posts_data = fetch_json_from_s3(posts_key)
                
                # Extract posts array (could be in different formats)
                posts = []
                if isinstance(posts_data, dict):
                    posts = posts_data.get("posts", [])
                    if not posts and isinstance(posts_data.get("data"), list):
                        posts = posts_data["data"]
                elif isinstance(posts_data, list):
                    posts = posts_data
                
                if not posts:
                    logger.warning(f"No posts found for profile {profile_id}")
                    processed_profiles.append({
                        "profile_id": profile_id,
                        "profile_name": profile_name,
                        "status": "skipped",
                        "reason": "no_posts",
                        "posts_classified": 0
                    })
                    continue
                
                # 4. Classify all posts
                logger.info(f"Classifying {len(posts)} posts for profile {profile_id}")
                classification_results = await classify_posts_batch(
                    posts=posts,
                    classifier_name=classifier_name,
                    classifier_prompt=classifier_prompt,
                    classifier_description=classifier_description,
                    classifier_labels=classifier_labels,
                    classifier_examples=classifier_examples,
                    batch_size=20,
                )
                
                # 5. Add labels to each post
                for idx, post in enumerate(posts):
                    if idx < len(classification_results):
                        classification = classification_results[idx]
                        # Create labels object with all scores
                        labels_obj = classification.get("allScores", {})
                        # Add classifierId to the labels object
                        labels_obj["classifierId"] = payload.classifierId
                        post["labels"] = labels_obj
                
                # Update the posts data structure
                if isinstance(posts_data, dict):
                    posts_data["posts"] = posts
                else:
                    posts_data = posts
                
                # 6. Upload updated posts back to S3
                updated_posts_url = upload_json_to_s3(posts_key, posts_data)
                
                # Update profile record with new posts URL
                database.update_audience_profile(profile_id, {"postsS3Url": updated_posts_url})
                
                total_posts_classified += len(posts)
                processed_profiles.append({
                    "profile_id": profile_id,
                    "profile_name": profile_name,
                    "status": "success",
                    "posts_classified": len(posts),
                    "updated_posts_url": updated_posts_url
                })
                
            except Exception as e:
                logger.error(f"Error processing profile {profile_id}: {e}")
                processed_profiles.append({
                    "profile_id": profile_id,
                    "profile_name": profile_name,
                    "status": "error",
                    "reason": str(e),
                    "posts_classified": 0
                })
        
        return {
            "classifier_id": payload.classifierId,
            "classifier_name": classifier_name,
            "audience_room_id": payload.audienceRoomId,
            "total_profiles_processed": len(profiles),
            "total_posts_classified": total_posts_classified,
            "profiles": processed_profiles
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error running classifier: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to run classifier: {str(e)}")


@router.post("/api/classifier/run-profiles")
async def run_classifier_for_profiles(payload: RunClassifierForProfilesRequest):
    """
    Run a classifier on posts for specific profiles in an audience room.
    
    Similar to /api/classifier/run but only processes the specified profiles.
    """
    ensure_db_available("audience")
    if not groq_client:
        raise HTTPException(status_code=503, detail="Groq client not initialized. Please set GROQ_API_KEY.")
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")
    
    try:
        # 1. Fetch Classifier details
        classifier = database.find_post_classifier_by_id(payload.classifierId)
        if not classifier:
            raise HTTPException(status_code=404, detail=f"Classifier {payload.classifierId} not found")
        
        # Extract classifier fields (same as run_classifier)
        classifier_name = classifier.name
        classifier_prompt = classifier.prompt or ""
        classifier_description = classifier.description or ""
        
        # Handle labels
        classifier_labels = []
        try:
            labels_raw = classifier.labels
            if isinstance(labels_raw, list):
                classifier_labels = labels_raw
            elif isinstance(labels_raw, str):
                try:
                    parsed = json.loads(labels_raw)
                    classifier_labels = parsed if isinstance(parsed, list) else [labels_raw]
                except (json.JSONDecodeError, TypeError):
                    classifier_labels = [labels_raw]
            elif isinstance(labels_raw, dict):
                if "labels" in labels_raw and isinstance(labels_raw["labels"], list):
                    classifier_labels = labels_raw["labels"]
                else:
                    classifier_labels = list(labels_raw.keys()) if labels_raw else []
            else:
                try:
                    classifier_labels = list(labels_raw) if labels_raw else []
                except (TypeError, ValueError):
                    classifier_labels = []
        except Exception as e:
            logger.warning(f"Error parsing classifier labels: {e}")
            classifier_labels = []
        
        classifier_labels = [str(label) for label in classifier_labels if label]
        
        if not classifier_labels:
            raise HTTPException(status_code=400, detail="Classifier has no labels defined")
        
        # Handle examples
        classifier_examples = None
        if classifier.examples:
            try:
                examples_raw = classifier.examples
                if isinstance(examples_raw, dict):
                    classifier_examples = examples_raw
                elif isinstance(examples_raw, str):
                    try:
                        classifier_examples = json.loads(examples_raw)
                    except json.JSONDecodeError:
                        classifier_examples = None
                elif isinstance(examples_raw, list):
                    classifier_examples = examples_raw
            except Exception as e:
                logger.warning(f"Error parsing classifier examples: {e}")
                classifier_examples = None
        
        # 2. Fetch AudienceRoom and specified Profiles
        audience_room = database.find_audience_room_by_id(payload.audienceRoomId, include_profiles=True)
        if not audience_room:
            raise HTTPException(status_code=404, detail=f"Audience room {payload.audienceRoomId} not found")
        
        # Filter profiles to only include the specified profile IDs
        all_profiles = audience_room.profiles
        profiles = [p for p in all_profiles if p.id in payload.profileIds]
        
        if not profiles:
            raise HTTPException(status_code=404, detail=f"No profiles found with the specified IDs in audience room {payload.audienceRoomId}")
        
        # Check if all requested profile IDs were found
        found_profile_ids = {p.id for p in profiles}
        missing_profile_ids = set(payload.profileIds) - found_profile_ids
        if missing_profile_ids:
            logger.warning(f"Some profile IDs were not found: {missing_profile_ids}")
        
        # 3. Process each profile's posts (same logic as run_classifier)
        processed_profiles = []
        total_posts_classified = 0
        
        for profile in profiles:
            profile_id = profile.id
            profile_name = profile.profileName
            
            if not profile.postsS3Url:
                logger.warning(f"Profile {profile_id} ({profile_name}) has no posts URL, skipping")
                processed_profiles.append({
                    "profile_id": profile_id,
                    "profile_name": profile_name,
                    "status": "skipped",
                    "reason": "no_posts_url",
                    "posts_classified": 0
                })
                continue
            
            try:
                posts_key = extract_s3_key_from_url(profile.postsS3Url)
                if not posts_key:
                    logger.error(f"Invalid S3 URL format for profile {profile_id}: {profile.postsS3Url}")
                    processed_profiles.append({
                        "profile_id": profile_id,
                        "profile_name": profile_name,
                        "status": "error",
                        "reason": "invalid_s3_url",
                        "posts_classified": 0
                    })
                    continue
                
                posts_data = fetch_json_from_s3(posts_key)
                
                posts = []
                if isinstance(posts_data, dict):
                    posts = posts_data.get("posts", [])
                    if not posts and isinstance(posts_data.get("data"), list):
                        posts = posts_data["data"]
                elif isinstance(posts_data, list):
                    posts = posts_data
                
                if not posts:
                    logger.warning(f"No posts found for profile {profile_id}")
                    processed_profiles.append({
                        "profile_id": profile_id,
                        "profile_name": profile_name,
                        "status": "skipped",
                        "reason": "no_posts",
                        "posts_classified": 0
                    })
                    continue
                
                logger.info(f"Classifying {len(posts)} posts for profile {profile_id}")
                classification_results = await classify_posts_batch(
                    posts=posts,
                    classifier_name=classifier_name,
                    classifier_prompt=classifier_prompt,
                    classifier_description=classifier_description,
                    classifier_labels=classifier_labels,
                    classifier_examples=classifier_examples,
                    batch_size=20,
                )
                
                for idx, post in enumerate(posts):
                    if idx < len(classification_results):
                        classification = classification_results[idx]
                        labels_obj = classification.get("allScores", {})
                        labels_obj["classifierId"] = payload.classifierId
                        post["labels"] = labels_obj
                
                if isinstance(posts_data, dict):
                    posts_data["posts"] = posts
                else:
                    posts_data = posts
                
                updated_posts_url = upload_json_to_s3(posts_key, posts_data)
                database.update_audience_profile(profile_id, {"postsS3Url": updated_posts_url})
                
                total_posts_classified += len(posts)
                processed_profiles.append({
                    "profile_id": profile_id,
                    "profile_name": profile_name,
                    "status": "success",
                    "posts_classified": len(posts),
                    "updated_posts_url": updated_posts_url
                })
                
            except Exception as e:
                logger.error(f"Error processing profile {profile_id}: {e}")
                processed_profiles.append({
                    "profile_id": profile_id,
                    "profile_name": profile_name,
                    "status": "error",
                    "reason": str(e),
                    "posts_classified": 0
                })
        
        return {
            "classifier_id": payload.classifierId,
            "classifier_name": classifier_name,
            "audience_room_id": payload.audienceRoomId,
            "requested_profile_ids": payload.profileIds,
            "total_profiles_processed": len(profiles),
            "total_posts_classified": total_posts_classified,
            "profiles": processed_profiles
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error running classifier for profiles: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to run classifier for profiles: {str(e)}")

