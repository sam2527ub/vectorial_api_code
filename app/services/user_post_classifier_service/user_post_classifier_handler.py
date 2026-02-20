"""Handler for User Post Classifier: trigger async classifier, process job, status."""
import asyncio
import json
import uuid
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from app.config import logger
from app.utils.helpers import ensure_db_available
from app.utils.s3_utils import extract_s3_key_from_url, fetch_json_from_s3, upload_json_to_s3
from app.services.classifier_service import classify_posts_batch
from app import database
from app.services.user_post_classifier_service.config import (
    UserPostClassifierConfig,
    DEFAULT_BATCH_SIZE,
    CLASSIFY_POSTS_BATCH_SIZE,
)
from app.services.user_post_classifier_service.repositories import (
    create_classifier_job,
    get_classifier_job,
    update_classifier_job,
    get_pending_classifier_jobs,
)


class UserPostClassifierHandler:
    """Trigger async classifier jobs, process in background, expose status."""

    def __init__(self, config: Optional[UserPostClassifierConfig] = None):
        self.config = config or UserPostClassifierConfig()
        self.batch_size = self.config.batch_size
        self.classify_batch_size = self.config.classify_batch_size

    def _validate(self) -> None:
        ensure_db_available("audience")
        from app.config import groq_client, s3_client, s3_bucket
        if not groq_client:
            raise HTTPException(status_code=503, detail="Groq client not initialized. Please set GROQ_API_KEY.")
        if not s3_client or not s3_bucket:
            raise HTTPException(status_code=503, detail="S3 is not configured.")

    def trigger_async_classifier(
        self,
        audience_room_id: str,
        classifier_id: str,
        enterprise_name: Optional[str] = None,
        batch_size: Optional[int] = None,
        task_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a classifier job and return response. Caller must add background task
        to run process_classifier_job(job_id, audience_room_id, classifier_id, enterprise_name, batch_size).
        """
        self._validate()
        classifier = database.find_post_classifier_by_id(classifier_id)
        if not classifier:
            raise HTTPException(status_code=404, detail=f"Classifier {classifier_id} not found")

        size = batch_size if batch_size is not None else self.batch_size
        job_id = str(uuid.uuid4())
        create_classifier_job(
            job_id=job_id,
            classifier_id=classifier_id,
            audience_room_id=audience_room_id,
            task_token=task_token,
            enterprise_name=enterprise_name,
        )
        return {
            "job_id": job_id,
            "status": "PENDING",
            "classifier_id": classifier_id,
            "audience_room_id": audience_room_id,
            "message": "Classifier job started. Use /api/classifier/async/status/{job_id} to check progress.",
        }

    def _parse_labels(self, labels_raw: Any, job_id: str) -> List[str]:
        """Parse classifier labels from various formats."""
        classifier_labels = []
        try:
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
        return [str(label) for label in classifier_labels if label]

    def _parse_examples(self, examples_raw: Any) -> Optional[Any]:
        """Parse classifier examples from various formats."""
        if not examples_raw:
            return None
        try:
            if isinstance(examples_raw, dict):
                return examples_raw
            if isinstance(examples_raw, str):
                return json.loads(examples_raw)
            if isinstance(examples_raw, list):
                return examples_raw
        except Exception:
            pass
        return None

    async def process_classifier_job(
        self,
        job_id: str,
        audience_room_id: str,
        classifier_id: str,
        enterprise_name: Optional[str],
        batch_size: int,
    ) -> None:
        """
        Background task: process classifier job in batches.
        Fetches classifier, room/profiles, classifies posts per profile, updates S3 and profile record.
        """
        try:
            logger.info(f"[ClassifierJob {job_id}] Starting classifier processing")

            classifier = database.find_post_classifier_by_id(classifier_id)
            if not classifier:
                update_classifier_job(job_id, enterprise_name, status="FAILED", error=f"Classifier {classifier_id} not found")
                return

            classifier_name = classifier.name
            classifier_prompt = classifier.prompt or ""
            classifier_description = classifier.description or ""
            classifier_labels = self._parse_labels(classifier.labels, job_id)
            if not classifier_labels:
                update_classifier_job(job_id, enterprise_name, status="FAILED", error="Classifier has no labels defined")
                return
            classifier_examples = self._parse_examples(classifier.examples)

            audience_room = database.find_audience_room_by_id(
                audience_room_id,
                include_profiles=True,
                enterprise_name=enterprise_name,
            )
            if not audience_room:
                update_classifier_job(job_id, enterprise_name, status="FAILED", error=f"Audience room {audience_room_id} not found")
                return

            profiles = audience_room.profiles or []
            total_profiles = len(profiles)
            update_classifier_job(job_id, enterprise_name, status="PROCESSING", total_profiles=total_profiles)
            logger.info(f"[ClassifierJob {job_id}] Processing {total_profiles} profiles in batches of {batch_size}")

            total_posts_classified = 0
            processed_profiles = 0

            for i in range(0, total_profiles, batch_size):
                batch_profiles = profiles[i : i + batch_size]
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
                        posts_key = extract_s3_key_from_url(profile.postsS3Url)
                        if not posts_key:
                            processed_profiles += 1
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
                            processed_profiles += 1
                            continue

                        classification_results = await classify_posts_batch(
                            posts=posts,
                            classifier_name=classifier_name,
                            classifier_prompt=classifier_prompt,
                            classifier_description=classifier_description,
                            classifier_labels=classifier_labels,
                            classifier_examples=classifier_examples,
                            batch_size=self.classify_batch_size,
                        )
                        for idx, post in enumerate(posts):
                            if idx < len(classification_results):
                                classification = classification_results[idx]
                                labels_obj = classification.get("allScores", {})
                                labels_obj["classifierId"] = classifier_id
                                post["labels"] = labels_obj
                        if isinstance(posts_data, dict):
                            posts_data["posts"] = posts
                        else:
                            posts_data = posts
                        updated_posts_url = upload_json_to_s3(posts_key, posts_data)
                        database.update_audience_profile(
                            profile_id,
                            {"postsS3Url": updated_posts_url},
                            enterprise_name=enterprise_name,
                        )
                        total_posts_classified += len(posts)
                    except Exception as e:
                        logger.error(f"[ClassifierJob {job_id}] Error processing profile {profile_id}: {e}")
                    processed_profiles += 1

                update_classifier_job(
                    job_id, enterprise_name,
                    processed_profiles=processed_profiles,
                    total_posts_classified=total_posts_classified,
                )
                await asyncio.sleep(0.5)

            update_classifier_job(
                job_id, enterprise_name,
                status="COMPLETED",
                processed_profiles=processed_profiles,
                total_posts_classified=total_posts_classified,
            )
            logger.info(f"[ClassifierJob {job_id}] Completed: {total_posts_classified} posts classified")

        except Exception as e:
            logger.error(f"[ClassifierJob {job_id}] Failed: {str(e)}", exc_info=True)
            update_classifier_job(job_id, enterprise_name, status="FAILED", error=str(e))

    def get_job_status(self, job_id: str, enterprise_name: Optional[str] = None) -> Dict[str, Any]:
        """Return job status dict. Raises HTTPException if not found."""
        job = get_classifier_job(job_id, enterprise_name)
        if not job:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
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
            "updated_at": job["updated_at"],
        }

    def get_pending_jobs(self, enterprise_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return all pending/processing classifier jobs."""
        return get_pending_classifier_jobs(enterprise_name)
