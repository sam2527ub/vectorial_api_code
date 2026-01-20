"""Async Classifier endpoints for long-running classification jobs."""
import json
import uuid
import asyncio
from datetime import datetime
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Path, Query, BackgroundTasks
from pydantic import BaseModel
from app.config import groq_client, s3_client, s3_bucket, logger
from app.utils.helpers import ensure_db_available
from app.utils.s3_utils import extract_s3_key_from_url, fetch_json_from_s3, upload_json_to_s3
from app.services.classifier_service import classify_posts_batch
from app import database

router = APIRouter()


class AsyncClassifierRequest(BaseModel):
    """Request model for async classifier trigger."""
    audienceRoomId: str
    classifierId: str
    enterpriseName: Optional[str] = None
    batchSize: int = 10  # Number of profiles per batch


class ClassifierJobStatus(BaseModel):
    """Status model for classifier job."""
    job_id: str
    status: str  # PENDING, PROCESSING, COMPLETED, FAILED
    classifier_id: str
    audience_room_id: str
    total_profiles: int = 0
    processed_profiles: int = 0
    total_posts_classified: int = 0
    error: Optional[str] = None
    created_at: str
    updated_at: str


# In-memory job storage (in production, use database or Redis)
_classifier_jobs: Dict[str, Dict[str, Any]] = {}


async def process_classifier_job(
    job_id: str,
    audience_room_id: str,
    classifier_id: str,
    enterprise_name: Optional[str],
    batch_size: int
):
    """
    Background task to process classifier job in batches.
    This runs asynchronously and updates job status as it progresses.
    """
    global _classifier_jobs
    
    try:
        logger.info(f"[ClassifierJob {job_id}] Starting classifier processing")
        
        # Fetch classifier details
        classifier = database.find_post_classifier_by_id(classifier_id)
        if not classifier:
            _classifier_jobs[job_id]["status"] = "FAILED"
            _classifier_jobs[job_id]["error"] = f"Classifier {classifier_id} not found"
            _classifier_jobs[job_id]["updated_at"] = datetime.utcnow().isoformat()
            return
        
        # Extract classifier fields
        classifier_name = classifier.name
        classifier_prompt = classifier.prompt or ""
        classifier_description = classifier.description or ""
        
        # Parse labels
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
        except Exception as e:
            logger.warning(f"[ClassifierJob {job_id}] Error parsing classifier labels: {e}")
            classifier_labels = []
        
        classifier_labels = [str(label) for label in classifier_labels if label]
        
        if not classifier_labels:
            _classifier_jobs[job_id]["status"] = "FAILED"
            _classifier_jobs[job_id]["error"] = "Classifier has no labels defined"
            _classifier_jobs[job_id]["updated_at"] = datetime.utcnow().isoformat()
            return
        
        # Parse examples
        classifier_examples = None
        if classifier.examples:
            try:
                examples_raw = classifier.examples
                if isinstance(examples_raw, dict):
                    classifier_examples = examples_raw
                elif isinstance(examples_raw, str):
                    classifier_examples = json.loads(examples_raw)
                elif isinstance(examples_raw, list):
                    classifier_examples = examples_raw
            except Exception:
                classifier_examples = None
        
        # Fetch audience room and profiles
        audience_room = database.find_audience_room_by_id(
            audience_room_id, 
            include_profiles=True, 
            enterprise_name=enterprise_name
        )
        if not audience_room:
            _classifier_jobs[job_id]["status"] = "FAILED"
            _classifier_jobs[job_id]["error"] = f"Audience room {audience_room_id} not found"
            _classifier_jobs[job_id]["updated_at"] = datetime.utcnow().isoformat()
            return
        
        profiles = audience_room.profiles or []
        total_profiles = len(profiles)
        
        _classifier_jobs[job_id]["total_profiles"] = total_profiles
        _classifier_jobs[job_id]["status"] = "PROCESSING"
        _classifier_jobs[job_id]["updated_at"] = datetime.utcnow().isoformat()
        
        logger.info(f"[ClassifierJob {job_id}] Processing {total_profiles} profiles in batches of {batch_size}")
        
        # Process profiles in batches
        total_posts_classified = 0
        processed_profiles = 0
        
        for i in range(0, total_profiles, batch_size):
            batch_profiles = profiles[i:i + batch_size]
            batch_num = i // batch_size + 1
            total_batches = (total_profiles + batch_size - 1) // batch_size
            
            logger.info(f"[ClassifierJob {job_id}] Processing batch {batch_num}/{total_batches}")
            
            for profile in batch_profiles:
                profile_id = profile.id
                profile_name = profile.profileName
                
                if not profile.postsS3Url:
                    processed_profiles += 1
                    continue
                
                try:
                    # Extract S3 key and fetch posts
                    posts_key = extract_s3_key_from_url(profile.postsS3Url)
                    if not posts_key:
                        processed_profiles += 1
                        continue
                    
                    posts_data = fetch_json_from_s3(posts_key)
                    
                    # Extract posts array
                    posts = []
                    if isinstance(posts_data, dict):
                        posts = posts_data.get("posts", [])
                        if not posts and isinstance(posts_data.get("data"), list):
                            posts = posts_data["data"]
                    elif isinstance(posts_data, list):
                        posts = posts_data
                    
                    if not posts:
                        processed_profiles += 1
                        continue
                    
                    # Classify posts
                    classification_results = await classify_posts_batch(
                        posts=posts,
                        classifier_name=classifier_name,
                        classifier_prompt=classifier_prompt,
                        classifier_description=classifier_description,
                        classifier_labels=classifier_labels,
                        classifier_examples=classifier_examples,
                        batch_size=20,
                    )
                    
                    # Add labels to posts
                    for idx, post in enumerate(posts):
                        if idx < len(classification_results):
                            classification = classification_results[idx]
                            labels_obj = classification.get("allScores", {})
                            labels_obj["classifierId"] = classifier_id
                            post["labels"] = labels_obj
                    
                    # Update posts data structure
                    if isinstance(posts_data, dict):
                        posts_data["posts"] = posts
                    else:
                        posts_data = posts
                    
                    # Upload updated posts back to S3
                    updated_posts_url = upload_json_to_s3(posts_key, posts_data)
                    
                    # Update profile record
                    database.update_audience_profile(
                        profile_id, 
                        {"postsS3Url": updated_posts_url}, 
                        enterprise_name=enterprise_name
                    )
                    
                    total_posts_classified += len(posts)
                    
                except Exception as e:
                    logger.error(f"[ClassifierJob {job_id}] Error processing profile {profile_id}: {e}")
                
                processed_profiles += 1
            
            # Update job progress after each batch
            _classifier_jobs[job_id]["processed_profiles"] = processed_profiles
            _classifier_jobs[job_id]["total_posts_classified"] = total_posts_classified
            _classifier_jobs[job_id]["updated_at"] = datetime.utcnow().isoformat()
            
            # Small delay between batches to avoid rate limits
            await asyncio.sleep(0.5)
        
        # Mark job as completed
        _classifier_jobs[job_id]["status"] = "COMPLETED"
        _classifier_jobs[job_id]["processed_profiles"] = processed_profiles
        _classifier_jobs[job_id]["total_posts_classified"] = total_posts_classified
        _classifier_jobs[job_id]["updated_at"] = datetime.utcnow().isoformat()
        
        logger.info(f"[ClassifierJob {job_id}] Completed: {total_posts_classified} posts classified")
        
    except Exception as e:
        logger.error(f"[ClassifierJob {job_id}] Failed: {str(e)}", exc_info=True)
        _classifier_jobs[job_id]["status"] = "FAILED"
        _classifier_jobs[job_id]["error"] = str(e)
        _classifier_jobs[job_id]["updated_at"] = datetime.utcnow().isoformat()


@router.post("/api/classifier/async/run")
async def trigger_async_classifier(
    payload: AsyncClassifierRequest,
    background_tasks: BackgroundTasks
):
    """
    Trigger async classifier job that runs in the background.
    Returns immediately with a job_id for polling status.
    
    This endpoint is designed for long-running classification jobs
    that would otherwise timeout with the synchronous endpoint.
    
    Request Body:
    - audienceRoomId: UUID of the audience room
    - classifierId: UUID of the classifier to use
    - enterpriseName (optional): Enterprise name for database selection
    - batchSize (optional): Number of profiles per batch (default: 10)
    """
    logger.info(f"=== ASYNC CLASSIFIER TRIGGER START ===")
    logger.info(f"Request: classifier_id={payload.classifierId}, room_id={payload.audienceRoomId}, enterprise={payload.enterpriseName}")
    
    ensure_db_available("audience")
    
    if not groq_client:
        raise HTTPException(status_code=503, detail="Groq client not initialized. Please set GROQ_API_KEY.")
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured.")
    
    # Generate job ID
    job_id = str(uuid.uuid4())
    
    # Create job record
    _classifier_jobs[job_id] = {
        "job_id": job_id,
        "status": "PENDING",
        "classifier_id": payload.classifierId,
        "audience_room_id": payload.audienceRoomId,
        "enterprise_name": payload.enterpriseName,
        "total_profiles": 0,
        "processed_profiles": 0,
        "total_posts_classified": 0,
        "error": None,
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat()
    }
    
    # Add background task
    background_tasks.add_task(
        process_classifier_job,
        job_id=job_id,
        audience_room_id=payload.audienceRoomId,
        classifier_id=payload.classifierId,
        enterprise_name=payload.enterpriseName,
        batch_size=payload.batchSize
    )
    
    logger.info(f"=== ASYNC CLASSIFIER TRIGGERED: job_id={job_id} ===")
    
    return {
        "job_id": job_id,
        "status": "PENDING",
        "message": "Classifier job started. Use /api/classifier/async/status/{job_id} to check progress."
    }


@router.get("/api/classifier/async/status/{job_id}")
async def get_async_classifier_status(
    job_id: str = Path(..., description="Job ID returned from trigger endpoint"),
    enterpriseName: Optional[str] = Query(None, description="Enterprise name")
):
    """
    Check the status of an async classifier job.
    
    Returns current progress including:
    - status: PENDING, PROCESSING, COMPLETED, FAILED
    - total_profiles: Total profiles to process
    - processed_profiles: Number of profiles processed so far
    - total_posts_classified: Total posts classified
    """
    logger.info(f"Checking async classifier status for job_id={job_id}")
    
    if job_id not in _classifier_jobs:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    
    job = _classifier_jobs[job_id]
    
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "classifier_id": job["classifier_id"],
        "audience_room_id": job["audience_room_id"],
        "total_profiles": job["total_profiles"],
        "processed_profiles": job["processed_profiles"],
        "total_posts_classified": job["total_posts_classified"],
        "error": job["error"],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"]
    }
