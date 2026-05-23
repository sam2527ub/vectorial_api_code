"""Status handler for comment context scraping jobs."""
import asyncio
from typing import Dict, Any, List
from fastapi import HTTPException
from app.config import logger
from app.services.comment_context_service.clients.storage import get_storage_client
from app.services.comment_context_service.clients.scraping import get_scraping_client
from app.services.comment_context_service.config import CommentContextServiceConfig
from app.services.comment_context_service.utils import (
    get_comment_context_meta_key,
    get_comment_context_run_key,
)
from app.services.comment_context_service.utils.post_url_utils import batch_post_urls


_TERMINAL_STATUSES = ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT")


class CommentContextStatusHandler:
    """Handles status polling and storing run results in S3."""

    def __init__(self):
        self.config = CommentContextServiceConfig()
        self.storage_client = get_storage_client()
        self.scraping_client = get_scraping_client(self.config)

    def _validate(self) -> None:
        if not self.storage_client.is_configured():
            raise HTTPException(status_code=503, detail="Storage client not configured.")
        if not self.scraping_client.is_configured():
            raise HTTPException(
                status_code=503,
                detail="Scraping client not configured. Set APIFY_API_TOKEN.",
            )

    async def _advance_starting_phase(
        self, meta: Dict[str, Any], meta_key: str
    ) -> None:
        """Start more Apify runs when under concurrency cap; update meta in place."""
        unique_urls = meta.get("unique_urls") or []
        post_urls_per_batch = meta.get("post_urls_per_batch") or self.config.post_urls_per_batch
        batches = batch_post_urls(unique_urls, post_urls_per_batch)
        run_ids: List[str] = list(meta.get("run_ids") or [])
        pending_batch_index = meta.get("pending_batch_index", 0)
        total_batches = meta.get("total_batches", 0)
        max_concurrent = self.config.max_concurrent_apify_runs

        running_count = 0
        for run_id in run_ids:
            try:
                status_data = await asyncio.to_thread(
                    self.scraping_client.get_job_status, run_id
                )
                st = (status_data.get("status") or "").upper()
                if st not in _TERMINAL_STATUSES:
                    running_count += 1
            except Exception as e:
                logger.warning(f"[CommentContext] Status check for run {run_id}: {e}")
                running_count += 1

        while running_count < max_concurrent and pending_batch_index < total_batches:
            batch = batches[pending_batch_index]
            run_id = await self.scraping_client.start_post_scraping_job(batch)
            run_ids.append(run_id)
            pending_batch_index += 1
            running_count += 1
            meta["run_ids"] = run_ids
            meta["pending_batch_index"] = pending_batch_index
            logger.info(
                f"[CommentContext] Started run {run_id} for {len(batch)} post URLs "
                f"({pending_batch_index}/{total_batches} batches)"
            )

        if pending_batch_index >= total_batches:
            meta["status"] = "running"
            logger.info(
                f"[CommentContext] All {total_batches} batches started; status -> running"
            )

        self.storage_client.write_json(meta_key, meta)

    async def check_status(
        self, audience_room_id: str, enterprise_name: str
    ) -> Dict[str, Any]:
        """Poll Apify for each run; fetch and store completed runs in S3."""
        self._validate()
        enterprise_name = enterprise_name or "default"

        meta_key = get_comment_context_meta_key(enterprise_name, audience_room_id)
        try:
            meta = self.storage_client.read_json(meta_key)
        except Exception as e:
            logger.warning(f"[CommentContext] Job meta not found: {meta_key} - {e}")
            raise HTTPException(status_code=404, detail=f"Job not found for audience room: {audience_room_id}.")

        audience_room_id = meta.get("audience_room_id")
        job_id = meta.get("job_id")
        run_ids: List[str] = list(meta.get("run_ids") or [])
        fetched: List[str] = list(meta.get("fetched_run_ids") or [])
        status = meta.get("status", "running")

        if status == "scraping_complete":
            return {
                "job_id": job_id,
                "audience_room_id": audience_room_id,
                "status": "scraping_complete",
                "run_ids": run_ids,
                "fetched_run_ids": fetched,
                "message": "Scraping complete. Use comment-context-summary service with audience_room_id.",
                "enterprise_name": meta.get("enterprise_name"),
            }

        if status == "starting":
            await self._advance_starting_phase(meta, meta_key)
            run_ids = meta.get("run_ids") or []
            pending = meta.get("pending_batch_index", 0)
            total_batches = meta.get("total_batches", 0)
            return {
                "job_id": job_id,
                "audience_room_id": audience_room_id,
                "status": "starting" if meta.get("status") == "starting" else "running",
                "run_ids": run_ids,
                "pending_batch_index": pending,
                "total_batches": total_batches,
                "message": "Poll again to start more runs." if meta.get("status") == "starting" else "All runs started. Poll again for results.",
                "enterprise_name": meta.get("enterprise_name"),
            }

        running_count = 0
        failed_count = 0
        run_details: List[Dict[str, Any]] = []

        for run_id in run_ids:
            if run_id in fetched:
                run_details.append({"run_id": run_id, "status": "fetched"})
                continue
            try:
                run_status = self.scraping_client.get_job_status(run_id)
                st = (run_status.get("status") or "").upper()
                if st == "SUCCEEDED":
                    results = self.scraping_client.fetch_job_results(run_id)
                    run_key = get_comment_context_run_key(enterprise_name, audience_room_id, run_id)
                    self.storage_client.write_json(run_key, {"items": results})
                    fetched.append(run_id)
                    run_details.append({
                        "run_id": run_id,
                        "status": "succeeded",
                        "items_count": len(results),
                    })
                    logger.info(f"[CommentContext] Fetched run {run_id}: {len(results)} items")
                elif st in ("FAILED", "ABORTED", "TIMED-OUT"):
                    failed_count += 1
                    run_details.append({
                        "run_id": run_id,
                        "status": st.lower(),
                        "error": run_status.get("statusMessage", "Unknown"),
                    })
                else:
                    running_count += 1
                    run_details.append({"run_id": run_id, "status": "running"})
            except Exception as e:
                logger.error(f"[CommentContext] Run {run_id} error: {e}", exc_info=True)
                run_details.append({"run_id": run_id, "status": "error", "error": str(e)})

        meta["fetched_run_ids"] = fetched
        if len(fetched) + failed_count >= len(run_ids):
            meta["status"] = "scraping_complete"
        self.storage_client.write_json(meta_key, meta)

        return {
            "job_id": job_id,
            "audience_room_id": audience_room_id,
            "status": "scraping_complete" if meta["status"] == "scraping_complete" else "running",
            "run_ids": run_ids,
            "fetched_run_ids": fetched,
            "running_runs": running_count,
            "failed_runs": failed_count,
            "run_details": run_details,
            "message": "Scraping complete." if meta["status"] == "scraping_complete" else "Still running. Poll again.",
            "enterprise_name": meta.get("enterprise_name"),
        }
