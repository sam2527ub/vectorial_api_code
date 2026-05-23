"""Status handler for user activity scraping jobs."""
import time
import uuid
from typing import Dict, Any, List, Set
from datetime import datetime
from fastapi import HTTPException
from app.config import logger
from app.services.user_observation_fetch_service.utils.result_processor import ResultProcessor
from app.services.user_observation_fetch_service.utils.username_utils import UsernameProcessor
from app.services.user_observation_fetch_service.clients.scraping.factory import get_scraping_client
from app.services.user_observation_fetch_service.clients.storage.factory import get_storage_client
from app.utils.s3_utils import upload_json_to_s3

class StatusHandler:
    """Handles status checking and result fetching for user activity scraping."""
    
    def __init__(self):
        """Initialize status handler."""
        self.result_processor = ResultProcessor()
        self.username_processor = UsernameProcessor()
    
    def _extract_usernames_from_urls(self, start_urls: List[str]) -> Set[str]:
        """Extract usernames from Reddit profile URLs."""
        usernames = set()
        for url in start_urls:
            if isinstance(url, str) and "/user/" in url:
                parts = url.rstrip("/").split("/user/")
                if len(parts) > 1:
                    username = parts[1].split("/")[0]
                    if username:
                        usernames.add(username)
        return usernames
    
    def _extract_usernames_from_run_input(self, run_status: Dict[str, Any]) -> Set[str]:
        """Extract requested usernames from run input."""
        usernames = set()
        run_input = run_status.get("defaultRunInput", {})
        if run_input and isinstance(run_input, dict):
            start_urls = run_input.get("startUrls", [])
            usernames.update(self._extract_usernames_from_urls(start_urls))
        return usernames
    
    def _fetch_run_activities(self, run_id: str) -> List[Dict[str, Any]]:
        """Fetch activities from completed job."""
        scraping_client = get_scraping_client()
        if not scraping_client.is_configured():
            return []
        try:
            return scraping_client.fetch_job_results(run_id)
        except Exception as e:
            logger.error(f"Error fetching activities for run {run_id}: {e}")
            return []
    
    async def check_batch_status(self, run_ids: List[str]) -> Dict[str, Any]:
        """Check status of multiple Apify runs and aggregate results."""
        start_time = time.time()
        all_raw_activities = []
        all_requested_usernames = set()
        
        successful_runs = 0
        failed_runs = 0
        running_runs = 0
        run_details = []
        
        scraping_client = get_scraping_client()
        if not scraping_client.is_configured():
            raise HTTPException(
                status_code=503,
                detail="Scraping client not configured. Please set APIFY_API_TOKEN environment variable."
            )
        
        for run_id in run_ids:
            try:
                run_status = scraping_client.get_job_status(run_id)
                status = run_status.get("status", "").upper()
                logger.info(f"Run {run_id} status: {status}")
                # Extract requested usernames from run input
                usernames = self._extract_usernames_from_run_input(run_status)
                all_requested_usernames.update(usernames)
                
                if status == "SUCCEEDED":
                    successful_runs += 1
                    activities = self._fetch_run_activities(run_id)
                    all_raw_activities.extend(activities)
                    
                    # Extract authors from activities
                    for activity in activities:
                        author = activity.get("author")
                        if author:
                            all_requested_usernames.add(author)
                    
                    run_details.append({
                        "run_id": run_id,
                        "status": "succeeded",
                        "activities_count": len(activities)
                    })
                    logger.info(f"Run {run_id}: fetched {len(activities)} activities")
                        
                elif status in ["FAILED", "ABORTED", "TIMED-OUT"]:
                    failed_runs += 1
                    error_msg = run_status.get("statusMessage", "Unknown error")
                    run_details.append({
                        "run_id": run_id,
                        "status": status.lower().replace("-", "_"),
                        "error": error_msg
                    })
                    logger.warning(f"Run {run_id} failed: {error_msg}")
                    
                elif status in ["RUNNING", "READY"]:
                    running_runs += 1
                    run_details.append({
                        "run_id": run_id,
                        "status": status.lower(),
                        "message": "Still processing"
                    })
                else:
                    run_details.append({
                        "run_id": run_id,
                        "status": status.lower() if status else "unknown"
                    })
                    
            except Exception as e:
                logger.error(f"Error checking run {run_id}: {e}", exc_info=True)
                failed_runs += 1
                run_details.append({
                    "run_id": run_id,
                    "status": "error",
                    "error": str(e)
                })
        
        # Determine overall status
        if running_runs > 0:
            return {
                "status": "running",
                "total_runs": len(run_ids),
                "successful_runs": successful_runs,
                "failed_runs": failed_runs,
                "running_runs": running_runs,
                "total_activities_so_far": len(all_raw_activities),
                "run_details": run_details,
                "message": "Scraping in progress. Please check again in a few seconds.",
                "checked_at": datetime.now().isoformat()
            }
        
        if failed_runs == len(run_ids):
            return {
                "status": "failed",
                "total_runs": len(run_ids),
                "successful_runs": 0,
                "failed_runs": failed_runs,
                "running_runs": 0,
                "total_users": 0,
                "successful_users": 0,
                "failed_users": 0,
                "total_activities": 0,
                "users": [],
                "activities": [],
                "run_details": run_details,
                "checked_at": datetime.now().isoformat()
            }
        
        overall_status = "succeeded" if successful_runs == len(run_ids) else "partial"
        
        # Transform to old format for downstream compatibility
        transformed_result = self.result_processor.transform_new_scraper_to_old_format(
            flat_activities=all_raw_activities,
            requested_usernames=list(all_requested_usernames)
        )
        
        # Store activities in S3 to avoid Vercel Workflow payload size limits
        activities_s3_url = None
        storage_client = get_storage_client()
        if not storage_client.is_configured():
            raise HTTPException(status_code=503, detail="Storage client not configured")

        try:
            if transformed_result["activities"]:
                temp_id = str(uuid.uuid4())
                s3_key = f"temp/user-activities/{temp_id}.json"
                activities_s3_url = upload_json_to_s3(
                    s3_key,
                    transformed_result["activities"],
                    s3_client=storage_client.get_raw_client(),
                    s3_bucket=storage_client.get_bucket_name(),
                    s3_region=storage_client.get_region()
                )
                logger.info(f"Stored {len(transformed_result['activities'])} activities in S3: {s3_key}")
        except Exception as s3_err:
            logger.warning(f"Failed to store activities in S3, returning in response: {s3_err}")
        
        total_time = time.time() - start_time
        logger.info(
            f"Batch status complete: {overall_status}, "
            f"{transformed_result['total_users']} users, "
            f"{transformed_result['total_activities']} activities "
            f"(total time: {total_time:.2f}s)"
        )
        
        # Build response
        response = {
            "status": overall_status,
            "total_runs": len(run_ids),
            "successful_runs": successful_runs,
            "failed_runs": failed_runs,
            "running_runs": running_runs,
            "total_users": transformed_result["total_users"],
            "successful_users": transformed_result["successful_users"],
            "failed_users": transformed_result["failed_users"],
            "total_activities": transformed_result["total_activities"],
            "users": transformed_result["users"],
            "run_details": run_details,
            "checked_at": datetime.now().isoformat()
        }
        
        # Include S3 URL if stored, otherwise include activities directly
        if activities_s3_url:
            response["activities_s3_url"] = activities_s3_url
            response["activities"] = []
        else:
            response["activities"] = transformed_result["activities"]
        
        return response
    
    async def check_single_run_status(self, run_id: str) -> Dict[str, Any]:
        """Check status of a single scraping job."""
        scraping_client = get_scraping_client()
        if not scraping_client.is_configured():
            raise HTTPException(
                status_code=503,
                detail="Scraping client not configured. Please set APIFY_API_TOKEN environment variable."
            )
        run_status = scraping_client.get_job_status(run_id)
        status = run_status.get("status", "").upper()
        
        result = {
            "run_id": run_id,
            "status": status.lower(),
            "checked_at": datetime.now().isoformat()
        }
        
        if status == "SUCCEEDED":
            try:
                activities = scraping_client.fetch_job_results(run_id)
                result["activities_count"] = len(activities)
            except Exception:
                pass
        elif status in ["FAILED", "ABORTED", "TIMED-OUT"]:
            result["error"] = run_status.get("statusMessage", "Unknown error")
        
        return result
