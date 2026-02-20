"""Status handler for parallel scrape jobs: poll batches, aggregate, process posts when done."""
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.config import apify_client, logger
from app.services.profile_service import process_posts_and_update_profiles
from app.services.user_observation_fetch_service.clients.scraping.factory import get_post_scraper_client
from app.services.user_observation_fetch_service.repositories import find_job_by_id, update_job


class ParallelScrapeStatusHandler:
    """Polls batch run statuses, updates job result, and processes posts when all batches are done."""

    def __init__(self):
        self.scraper_client = get_post_scraper_client()

    async def check_job_status(
        self,
        job_id: str,
        enterprise_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Load job from DB; if COMPLETED/FAILED return cached; else poll each batch,
        update job_result; when all done, process posts and update job. Return same response shape.
        """
        db_job = find_job_by_id(job_id, enterprise_name=enterprise_name)
        if not db_job:
            return {"_not_found": True}

        job_result = db_job.result or {}
        if not job_result.get("parallel_mode"):
            return {"_bad_request": True, "message": f"Job {job_id} is not a parallel scrape job"}

        if db_job.status in ("COMPLETED", "FAILED"):
            return {
                "job_id": job_id,
                "status": db_job.status,
                "total_urls": job_result.get("total_urls", 0),
                "total_batches": job_result.get("total_batches", 0),
                "completed_batches": job_result.get("completed_batches", 0),
                "failed_batches": job_result.get("failed_batches", 0),
                "still_running": 0,
                "result": job_result.get("final_result"),
                "created_at": db_job.createdAt.isoformat() if db_job.createdAt else None,
                "updated_at": db_job.updatedAt.isoformat() if db_job.updatedAt else None,
            }

        batches = job_result.get("batches", [])
        completed = 0
        failed = 0
        still_running = 0
        all_dataset_ids: List[str] = []

        for batch in batches:
            if batch.get("status") in ("SUCCEEDED", "COMPLETED"):
                completed += 1
                if batch.get("dataset_id"):
                    all_dataset_ids.append(batch["dataset_id"])
            elif batch.get("status") == "FAILED":
                failed += 1
            elif batch.get("apify_run_id"):
                run_info = self.scraper_client.get_run_status(batch["apify_run_id"])
                run_status = run_info.get("status")
                if run_status == "SUCCEEDED":
                    batch["status"] = "SUCCEEDED"
                    batch["dataset_id"] = run_info.get("defaultDatasetId")
                    completed += 1
                    if batch.get("dataset_id"):
                        all_dataset_ids.append(batch["dataset_id"])
                elif run_status == "FAILED":
                    batch["status"] = "FAILED"
                    batch["error"] = run_info.get("statusMessage") or run_info.get("error") or "Unknown error"
                    failed += 1
                else:
                    still_running += 1
            else:
                still_running += 1

        job_result["completed_batches"] = completed
        job_result["failed_batches"] = failed
        job_result["batches"] = batches
        job_result["updated_at"] = datetime.utcnow().isoformat()

        final_status = db_job.status
        final_result: Optional[Dict[str, Any]] = None

        if still_running == 0:
            if failed == len(batches):
                final_status = "FAILED"
                final_result = {"error": "All batches failed"}
            else:
                total_posts = 0
                total_profiles_updated = 0
                for dataset_id in all_dataset_ids:
                    try:
                        if apify_client:
                            dataset_client = apify_client.dataset(dataset_id)
                            processing_result = await process_posts_and_update_profiles(
                                dataset_client=dataset_client,
                                job_id=job_id,
                                audience_room_id=job_result.get("audience_room_id"),
                                linkedin_urls=job_result.get("linkedin_urls"),
                                enterprise_name=enterprise_name,
                            )
                            total_posts += processing_result.get("posts_found", 0)
                            total_profiles_updated += processing_result.get("profiles_updated", 0)
                    except Exception as e:
                        logger.error(f"Error processing dataset {dataset_id}: {e}")
                final_status = "COMPLETED"
                final_result = {
                    "posts_found": total_posts,
                    "profiles_updated": total_profiles_updated,
                    "datasets_processed": len(all_dataset_ids),
                }
            job_result["final_result"] = final_result

        try:
            update_job(job_id, {"status": final_status, "result": job_result}, enterprise_name=enterprise_name)
        except Exception as e:
            logger.warning(f"Failed to update job status in database: {e}")

        response = {
            "job_id": job_id,
            "status": final_status,
            "total_urls": job_result.get("total_urls", 0),
            "total_batches": job_result.get("total_batches", 0),
            "completed_batches": completed,
            "failed_batches": failed,
            "still_running": still_running,
            "result": final_result,
            "created_at": db_job.createdAt.isoformat() if db_job.createdAt else None,
            "updated_at": datetime.utcnow().isoformat(),
        }
        if final_result:
            response["posts_found"] = final_result.get("posts_found", 0)
            response["profiles_updated"] = final_result.get("profiles_updated", 0)
            if "error" in final_result:
                response["error"] = final_result["error"]
        return response
