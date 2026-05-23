"""Summary job handler - manages job status and batch processing."""
import json
import uuid
from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple
from fastapi import HTTPException
from app.config import logger
from app.services.user_profile_summarization_service.repository import (
    find_audience_room_by_id,
    create_summary_job,
    find_summary_job_by_id,
    update_summary_job
)
from app.database.repositories.shared_repositories import find_audience_profile_by_id
from app.services.user_profile_summarization_service.profile_summary_processor import ProfileProcessor


class SummaryJobHandler:
    """Handles summary generation job operations."""
    
    def __init__(self):
        """Initialize job handler."""
        self.profile_processor = ProfileProcessor()
        self.batch_size = 3
    
    def start_job(
        self,
        audience_room_id: str,
        enterprise_name: Optional[str]
    ) -> Dict[str, Any]:
        """
        Start a new summary generation job.
        """
        # Fetch audience room and profiles
        audience_room = find_audience_room_by_id(
            audience_room_id,
            include_profiles=True,
            enterprise_name=enterprise_name
        )
        if not audience_room:
            raise HTTPException(
                status_code=404,
                detail=f"Audience room {audience_room_id} not found"
            )
        
        profiles = audience_room.profiles
        if not profiles:
            raise HTTPException(
                status_code=404,
                detail=f"No profiles found in audience room {audience_room_id}"
            )
        
        job_id = str(uuid.uuid4())
        
        # Extract profile IDs for tracking
        profile_ids = [profile.id for profile in profiles]
        
        # Create job record in database
        summary_job = create_summary_job(
            job_id=job_id,
            audience_room_id=audience_room_id,
            total_profiles=len(profiles),
            profile_ids=profile_ids,
            enterprise_name=enterprise_name
        )
        
        logger.info(
            f"Created summary generation job {job_id} for {len(profiles)} profiles "
            f"in audience room {audience_room_id}"
        )
        
        return {
            "job_id": job_id,
            "audience_room_id": audience_room_id,
            "status": "started",
            "total_profiles": len(profiles),
            "check_status_url": f"/api/v1/audience-rooms/{audience_room_id}/generate-summaries/status",
            "started_at": summary_job.startedAt.isoformat() if summary_job.startedAt else datetime.utcnow().isoformat(),
            "message": f"Summary generation job created. Use POST /api/v1/audience-rooms/{audience_room_id}/generate-summaries/status with job_id to poll and process batches."
        }
    
    def _parse_job_profiles_data(self, job_profiles: Any, job_id: str) -> Tuple[List[str], List[Dict[str, Any]]]:
        """Parse and validate job profiles data."""
        if isinstance(job_profiles, str):
            try:
                job_profiles = json.loads(job_profiles)
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse job.profiles as JSON: {job_profiles}")
                job_profiles = {}
        elif job_profiles is None:
            job_profiles = {}
        
        job_data = job_profiles if isinstance(job_profiles, dict) else {}
        profile_ids = job_data.get("profile_ids", [])
        existing_results = job_data.get("results", [])
        
        if not profile_ids:
            logger.error(
                f"Job {job_id} has no profile_ids stored! "
                f"job.profiles type: {type(job_profiles)}, value: {job_profiles}"
            )
            raise HTTPException(
                status_code=500,
                detail=f"Job {job_id} is missing profile_ids. This should not happen."
            )
        
        return profile_ids, existing_results
    
    def _get_remaining_profile_ids(
        self, profile_ids: List[str], existing_results: List[Dict[str, Any]], job_id: str
    ) -> List[str]:
        """Get remaining profile IDs to process."""
        processed_profile_ids = {
            r.get("profile_id") for r in existing_results if r.get("profile_id")
        }
        remaining_profile_ids = [
            pid for pid in profile_ids if pid not in processed_profile_ids
        ]
        
        logger.info(
            f"Job {job_id}: {len(processed_profile_ids)} processed, "
            f"{len(remaining_profile_ids)} remaining out of {len(profile_ids)} total"
        )
        
        return remaining_profile_ids
    
    async def _process_batch(
        self,
        batch_profile_ids: List[str],
        audience_room_id: str,
        enterprise_name: Optional[str]
    ) -> List[Dict[str, Any]]:
        """Process a batch of profiles."""
        batch_profiles = []
        for profile_id in batch_profile_ids:
            profile = find_audience_profile_by_id(profile_id, enterprise_name=enterprise_name)
            if profile:
                batch_profiles.append(profile)
        
        batch_results = []
        for profile in batch_profiles:
            try:
                result = await self.profile_processor.process_profile(profile, audience_room_id)
                batch_results.append(result)
            except Exception as e:
                logger.error(f"Error processing profile {profile.id}: {e}")
                batch_results.append({
                    "profile_id": profile.id,
                    "profile_name": profile.profileName,
                    "status": "error",
                    "reason": "exception",
                    "error": str(e)
                })
        
        return batch_results
    
    def _update_job_counts(
        self, batch_results: List[Dict[str, Any]], job: Any
    ) -> Tuple[int, int, int]:
        """Calculate updated job counts from batch results."""
        success_count = job.successCount
        skipped_count = job.skippedCount
        error_count = job.errorCount
        
        for result in batch_results:
            if result["status"] == "success":
                success_count += 1
            elif result["status"] == "skipped":
                skipped_count += 1
            else:
                error_count += 1
        
        return success_count, skipped_count, error_count
    
    def _build_running_response(self, job_id: str, audience_room_id: str, job: Any) -> Dict[str, Any]:
        """Build response for running job status."""
        logger.info(
            f"Summary job {job_id} progress: "
            f"{job.processedProfiles}/{job.totalProfiles} profiles processed, "
            f"{job.successCount} success, {job.skippedCount} skipped, "
            f"{job.errorCount} errors"
        )
        return {
            "job_id": job_id,
            "audience_room_id": audience_room_id,
            "status": "running",
            "total_profiles": job.totalProfiles,
            "processed_profiles": job.processedProfiles,
            "success_count": job.successCount,
            "skipped_count": job.skippedCount,
            "error_count": job.errorCount,
            "started_at": job.startedAt.isoformat() if job.startedAt else None,
            "updated_at": job.updatedAt.isoformat() if job.updatedAt else None,
            "shouldContinue": True,
            "message": f"Processing: {job.processedProfiles}/{job.totalProfiles} profiles completed",
        }
    
    def _build_completed_response(self, job_id: str, audience_room_id: str, job: Any) -> Dict[str, Any]:
        """Build response for completed job status."""
        logger.info(
            f"Summary job {job_id} completed: "
            f"{job.successCount} success, {job.skippedCount} skipped, "
            f"{job.errorCount} errors out of {job.totalProfiles} total profiles"
        )
        job_data = job.profiles or {}
        results = (
            job_data.get("results", [])
            if isinstance(job_data, dict)
            else (job.profiles if isinstance(job.profiles, list) else [])
        )
        
        return {
            "job_id": job_id,
            "audience_room_id": audience_room_id,
            "status": "completed",
            "total_profiles": job.totalProfiles,
            "success_count": job.successCount,
            "skipped_count": job.skippedCount,
            "error_count": job.errorCount,
            "profiles": results,
            "started_at": job.startedAt.isoformat() if job.startedAt else None,
            "completed_at": job.completedAt.isoformat() if job.completedAt else None,
            "updated_at": job.updatedAt.isoformat() if job.updatedAt else None,
            "shouldContinue": False,
        }
    
    def _build_failed_response(self, job_id: str, audience_room_id: str, job: Any) -> Dict[str, Any]:
        """Build response for failed job status."""
        logger.error(f"Summary job {job_id} failed: {job.error or 'Unknown error'}")
        return {
            "job_id": job_id,
            "audience_room_id": audience_room_id,
            "status": "failed",
            "error": job.error or "Unknown error",
            "started_at": job.startedAt.isoformat() if job.startedAt else None,
            "updated_at": job.updatedAt.isoformat() if job.updatedAt else None,
            "shouldContinue": False,
        }
    
    async def check_status(
        self,
        job_id: str,
        audience_room_id: str,
        enterprise_name: Optional[str]
    ) -> Dict[str, Any]:
        """
        Check job status and process next batch if running.
        """
        # Fetch job from database
        job = find_summary_job_by_id(job_id, enterprise_name=enterprise_name)
        if not job:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
        
        # Verify audience_room_id matches
        if job.audienceRoomId != audience_room_id:
            raise HTTPException(
                status_code=400,
                detail="Job ID does not match audience room ID"
            )
        
        status = job.status
        
        if status == "running":
            try:
                profile_ids, existing_results = self._parse_job_profiles_data(job.profiles, job_id)
                remaining_profile_ids = self._get_remaining_profile_ids(
                    profile_ids, existing_results, job_id
                )
                
                if remaining_profile_ids:
                    batch_profile_ids = remaining_profile_ids[:self.batch_size]
                    logger.info(
                        f"Processing batch of {len(batch_profile_ids)} profiles for job {job_id} "
                        f"({job.processedProfiles}/{job.totalProfiles} total)"
                    )
                    
                    batch_results = await self._process_batch(
                        batch_profile_ids, audience_room_id, enterprise_name
                    )
                    
                    success_count, skipped_count, error_count = self._update_job_counts(
                        batch_results, job
                    )
                    
                    all_results = existing_results + batch_results
                    new_processed_count = job.processedProfiles + len(batch_results)
                    remaining_after_batch = len(remaining_profile_ids) - len(batch_profile_ids)
                    is_complete = remaining_after_batch == 0
                    
                    update_summary_job(
                        job_id=job_id,
                        processed_profiles=new_processed_count,
                        success_count=success_count,
                        skipped_count=skipped_count,
                        error_count=error_count,
                        profiles={"profile_ids": profile_ids, "results": all_results},
                        status="completed" if is_complete else "running",
                        completed=is_complete,
                        enterprise_name=enterprise_name
                    )
                    
                    job = find_summary_job_by_id(job_id, enterprise_name=enterprise_name)
                    if not job:
                        raise HTTPException(
                            status_code=404, detail=f"Job {job_id} not found after update"
                        )
                    
                    logger.info(
                        f"Batch complete for job {job_id}: "
                        f"{job.processedProfiles}/{job.totalProfiles} profiles processed, "
                        f"{job.successCount} success, {job.skippedCount} skipped, "
                        f"{job.errorCount} errors"
                    )
                    
                    if is_complete:
                        status = "completed"
                else:
                    update_summary_job(
                        job_id=job_id,
                        status="completed",
                        completed=True,
                        enterprise_name=enterprise_name
                    )
                    status = "completed"
                    job = find_summary_job_by_id(job_id, enterprise_name=enterprise_name)
                    
            except Exception as e:
                logger.error(f"Error processing batch for job {job_id}: {e}", exc_info=True)
                update_summary_job(
                    job_id=job_id,
                    status="failed",
                    error=str(e),
                    enterprise_name=enterprise_name
                )
                status = "failed"
                job = find_summary_job_by_id(job_id, enterprise_name=enterprise_name)
        
        if status == "running":
            return self._build_running_response(job_id, audience_room_id, job)
        
        if status == "completed":
            return self._build_completed_response(job_id, audience_room_id, job)
        
        if status == "failed":
            return self._build_failed_response(job_id, audience_room_id, job)
        
        return {
            "job_id": job_id,
            "audience_room_id": audience_room_id,
            "status": status,
            "updated_at": job.updatedAt.isoformat() if job.updatedAt else None,
        }
