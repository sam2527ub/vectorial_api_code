"""Process a single classifier job: load room/profiles, classify per profile, update S3 and DB."""
import asyncio
from typing import Any, Dict, List, Optional

from app.config import logger
from app import database
from app.utils.s3_utils import extract_s3_key_from_url, fetch_json_from_s3, upload_json_to_s3

from .batch_runner import classify_posts_batch
from .config import UserPostClassifierConfig
from .repositories import (
    get_classifier_job,
    update_classifier_job,
)
from .utils.parser_helpers import parse_labels, parse_examples


async def process_classifier_job(
    job_id: str,
    audience_room_id: str,
    classifier_id: str,
    enterprise_name: Optional[str],
    batch_size: int,
    config: Optional[UserPostClassifierConfig] = None,
) -> None:
    """
    Run classifier job: for each profile load posts from S3, classify, merge labels, upload, update profile.
    """
    cfg = config or UserPostClassifierConfig()
    classify_batch_size = cfg.classify_batch_size

    try:
        logger.info(f"[ClassifierJob {job_id}] Starting")

        classifier = database.find_post_classifier_by_id(classifier_id)
        if not classifier:
            update_classifier_job(job_id, enterprise_name, status="FAILED", error=f"Classifier {classifier_id} not found")
            return

        classifier_name = classifier.name
        classifier_prompt = classifier.prompt or ""
        classifier_description = classifier.description or ""
        classifier_labels = parse_labels(classifier.labels, job_id)
        if not classifier_labels:
            update_classifier_job(job_id, enterprise_name, status="FAILED", error="Classifier has no labels")
            return
        classifier_examples = parse_examples(classifier.examples)

        room = database.find_audience_room_by_id(
            audience_room_id, include_profiles=True, enterprise_name=enterprise_name
        )
        if not room:
            update_classifier_job(job_id, enterprise_name, status="FAILED", error=f"Room {audience_room_id} not found")
            return

        profiles = room.profiles or []
        total_profiles = len(profiles)
        update_classifier_job(job_id, enterprise_name, status="PROCESSING", total_profiles=total_profiles)
        logger.info(f"[ClassifierJob {job_id}] {total_profiles} profiles, batch size {batch_size}")

        total_posts_classified = 0
        processed_profiles = 0

        for i in range(0, total_profiles, batch_size):
            batch_profiles = profiles[i : i + batch_size]
            for profile in batch_profiles:
                profile_id = profile.id
                if not profile.postsS3Url:
                    processed_profiles += 1
                    continue
                try:
                    posts_key = extract_s3_key_from_url(profile.postsS3Url)
                    if not posts_key:
                        processed_profiles += 1
                        continue
                    posts_data = fetch_json_from_s3(posts_key)
                    posts = _posts_from_data(posts_data)
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
                        batch_size=classify_batch_size,
                    )
                    for idx, post in enumerate(posts):
                        if idx < len(classification_results):
                            labels_obj = classification_results[idx].get("allScores", {}).copy()
                            labels_obj["classifierId"] = classifier_id
                            post["labels"] = labels_obj

                    if isinstance(posts_data, dict):
                        posts_data["posts"] = posts
                    else:
                        posts_data = posts
                    updated_url = upload_json_to_s3(posts_key, posts_data)
                    database.update_audience_profile(
                        profile_id, {"postsS3Url": updated_url}, enterprise_name=enterprise_name
                    )
                    total_posts_classified += len(posts)
                except Exception as e:
                    logger.error(f"[ClassifierJob {job_id}] Profile {profile_id}: {e}")
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
        logger.error(f"[ClassifierJob {job_id}] Failed: {e}", exc_info=True)
        update_classifier_job(job_id, enterprise_name, status="FAILED", error=str(e))


def _posts_from_data(posts_data: Any) -> List[Dict]:
    """Extract posts list from S3 payload (dict with 'posts' or 'data', or list)."""
    if isinstance(posts_data, dict):
        posts = posts_data.get("posts", [])
        if not posts and isinstance(posts_data.get("data"), list):
            posts = posts_data["data"]
        return posts if isinstance(posts, list) else []
    if isinstance(posts_data, list):
        return posts_data
    return []
