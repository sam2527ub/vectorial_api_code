"""
User Comments Fetch: start Apify comments run, poll by audience_room_id.
Job metadata stored in S3 under room folder: linkedin-comment-context/job.json.
LinkedIn URLs from room profiles; on success update AudienceProfile.commentsS3Url.
"""
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import HTTPException

from app import database
from app.config import logger
from app.utils.helpers import ensure_db_available
from app.services.user_comments_fetch_service.config import UserCommentsFetchConfig
from app.services.user_comments_fetch_service.clients.scraping.factory import get_comment_scraper_client
from app.services.user_comments_fetch_service.comment_processor import process_comments_and_update_profiles
from app.services.user_comments_fetch_service.utils.url_normalizer import normalize_profile_url
from app.services.user_comments_fetch_service.job_storage import get_job_s3_key, save_job, load_job


class UserCommentsFetchHandler:
    """Start comment scrape run from audience_room_id (URLs from room); poll Apify; on success update profile commentsS3Url."""

    def __init__(self):
        self.config = UserCommentsFetchConfig()
        self.client = get_comment_scraper_client(self.config)

    def _validate(self) -> None:
        ensure_db_available("audience")
        if not self.client.is_configured():
            raise HTTPException(status_code=503, detail="Apify client not initialized. Set APIFY_API_TOKEN.")

    def start_run(
        self,
        audience_room_id: str,
        enterprise_name: Optional[str] = None,
        max_items: int = 40,
        posted_limit: str = "any",
    ) -> Dict[str, Any]:
        """Load room profiles, split into batches of 20, start one Apify run per batch (parallel on Apify). Returns { run_id, status, message }."""
        self._validate()
        room = database.find_audience_room_by_id(
            audience_room_id, include_profiles=True, enterprise_name=enterprise_name
        )
        if not room:
            raise HTTPException(status_code=404, detail=f"Audience room {audience_room_id} not found")
        if not getattr(room, "profiles", None):
            raise HTTPException(status_code=400, detail=f"Audience room {audience_room_id} has no profiles")
        profiles = [normalize_profile_url(p.profileUrl) for p in room.profiles]
        profiles = [u for u in profiles if u]
        if not profiles:
            raise HTTPException(status_code=400, detail=f"No valid LinkedIn profile URLs in room {audience_room_id}")

        batch_size = self.config.profiles_per_batch
        batches = [profiles[i : i + batch_size] for i in range(0, len(profiles), batch_size)]
        run_ids: list[str] = []
        for batch in batches:
            result = self.client.start_run(profiles=batch, max_items=max_items, posted_limit=posted_limit)
            if result.get("status") == "FAILED":
                raise HTTPException(status_code=502, detail=result.get("error", "Failed to start Apify run"))
            rid = result.get("run_id")
            if not rid:
                raise HTTPException(status_code=502, detail="No run_id from Apify")
            run_ids.append(rid)

        job_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        source = getattr(room, "source", None)
        job = {
            "job_id": job_id,
            "run_ids": run_ids,
            "audience_room_id": audience_room_id,
            "enterprise_name": enterprise_name,
            "status": "PROCESSING",
            "batches_total": len(run_ids),
            "batches_completed": 0,
            "error": None,
            "created_at": now,
            "updated_at": now,
            "final_result": None,
        }
        try:
            key = get_job_s3_key(audience_room_id, enterprise_name, source)
            save_job(key, job)
        except Exception as e:
            logger.exception("Failed to save comment job to S3")
            raise HTTPException(status_code=500, detail=f"Failed to save job to S3: {e}")

        return {
            "job_id": job_id,
            "status": "PROCESSING",
            "message": f"Poll GET /api/v1/comments/scrape/status?audience_room_id={audience_room_id}"
            + ("&enterprise_name=" + enterprise_name if enterprise_name else ""),
        }

    def check_status(
        self,
        audience_room_id: str,
        enterprise_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Load job from S3 by audience_room_id. Poll Apify for stored run_ids.
        When all SUCCEEDED, merge datasets and update AudienceProfile.commentsS3Url; update job in S3.
        """
        self._validate()
        room = database.find_audience_room_by_id(
            audience_room_id, include_profiles=False, enterprise_name=enterprise_name
        )
        if not room:
            raise HTTPException(status_code=404, detail=f"Audience room {audience_room_id} not found")
        source = getattr(room, "source", None)
        key = get_job_s3_key(audience_room_id, enterprise_name, source)
        job = load_job(key)
        if not job:
            raise HTTPException(
                status_code=404,
                detail="No comment scrape job found for this audience room. Start one with POST /api/v1/comments/scrape.",
            )
        run_ids = job.get("run_ids") or []
        job_id = job.get("job_id", "")
        if not run_ids:
            return {
                "job_id": job_id,
                "status": "FAILED",
                "profiles_count": 0,
                "error": job.get("error") or "No run_ids in job",
            }

        completed = 0
        failed = 0
        all_dataset_ids: list[str] = []
        first_error: Optional[str] = None

        for rid in run_ids:
            run = self.client.get_run_status(rid)
            status = run.get("status")
            err = run.get("error")
            if status == "FAILED":
                failed += 1
                if err and not first_error:
                    first_error = err
            elif status == "SUCCEEDED":
                completed += 1
                did = run.get("defaultDatasetId")
                if did:
                    all_dataset_ids.append(did)

        now = datetime.now(timezone.utc).isoformat()
        job["updated_at"] = now
        job["batches_completed"] = completed

        if failed > 0 and failed + completed == len(run_ids):
            job["status"] = "FAILED"
            job["error"] = first_error or "One or more batch runs failed"
            try:
                save_job(key, job)
            except Exception:
                pass
            return {
                "job_id": job_id,
                "status": "FAILED",
                "profiles_count": 0,
                "error": job["error"],
            }
        if completed < len(run_ids):
            try:
                save_job(key, job)
            except Exception:
                pass
            return {
                "job_id": job_id,
                "status": "RUNNING",
                "profiles_count": 0,
                "batches_completed": completed,
                "batches_total": len(run_ids),
            }

        raw_items: list = []
        for did in all_dataset_ids:
            raw_items.extend(self.client.get_dataset_items(did))
        if not raw_items:
            job["status"] = "SUCCEEDED"
            job["final_result"] = {"comments_found": 0, "profiles_updated": 0}
            try:
                save_job(key, job)
            except Exception:
                pass
            return {
                "job_id": job_id,
                "status": "SUCCEEDED",
                "profiles_count": 0,
                "comments_found": 0,
                "profiles_updated": 0,
                "result": job["final_result"],
                "batches_total": len(run_ids),
            }

        result = process_comments_and_update_profiles(
            raw_items=raw_items,
            audience_room_id=audience_room_id,
            enterprise_name=enterprise_name,
        )
        job["status"] = "SUCCEEDED"
        job["final_result"] = result
        job["error"] = None
        try:
            save_job(key, job)
        except Exception:
            pass
        return {
            "job_id": job_id,
            "status": "SUCCEEDED",
            "profiles_count": len(raw_items),
            "comments_found": result.get("comments_found", 0),
            "profiles_updated": result.get("profiles_updated", 0),
            "result": result,
            "batches_total": len(run_ids),
        }
