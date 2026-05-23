"""Request handler for subreddit similarity filter endpoint."""
import uuid
from datetime import datetime
from typing import Dict, Any, List, Optional
from fastapi import HTTPException
from app.config import logger
from app.utils.s3_utils import upload_json_to_s3, fetch_json_from_s3, extract_s3_key_from_url
from app.services.subreddit_similarity_filter_service.clients.storage.factory import get_storage_client
from app.services.subreddit_similarity_filter_service.utils.filter_processor import FilterProcessor
from app.services.subreddit_similarity_filter_service.subreddit_similarity_checker import SimilarityChecker
from app.services.subreddit_similarity_filter_service.config import SubredditFilterConfig, DEFAULT_MIN_ACTIVITIES
from app.services.subreddit_similarity_filter_service.job_status_manager import JobStatusManager
from app.services.subreddit_similarity_filter_service.chunk_orchestrator import ChunkOrchestrator
from app.services.subreddit_similarity_filter_service.utils.data_extractors import normalize_subreddit_name


class SubredditSimilarityFilterHandler:
    """Handles subreddit similarity filter requests."""

    def __init__(self, similarity_checker: SimilarityChecker):
        self.filter_processor = FilterProcessor(similarity_checker)
        self.config = SubredditFilterConfig()
        self.storage_client = get_storage_client()

    # ── Legacy sync endpoint (kept for backwards compat) ─────────────────────

    async def handle_request(
        self,
        matched_subreddits: List[str],
        users: List[Dict[str, Any]],
        activities: Optional[List[Dict[str, Any]]],
        activities_s3_url: Optional[str],
        min_activities: int,
    ) -> Dict[str, Any]:
        """Handle filter request (sync — processes everything in a single response)."""
        fetched_activities = activities or []
        if activities_s3_url:
            logger.info(f"Fetching activities from S3: {activities_s3_url}")
            s3_key = extract_s3_key_from_url(activities_s3_url)
            if s3_key:
                fetched_activities = fetch_json_from_s3(
                    s3_key,
                    s3_client=self.storage_client.get_raw_client(),
                    s3_bucket=self.storage_client.get_bucket_name(),
                )
                logger.info(f"Fetched {len(fetched_activities)} activities from S3")
            else:
                raise HTTPException(status_code=400, detail="Invalid S3 URL for activities")

        if not fetched_activities:
            raise HTTPException(
                status_code=400,
                detail="No activities provided (either activities array or activities_s3_url required)",
            )

        user_activities_map = self.filter_processor.group_activities_by_username(fetched_activities)
        all_user_subreddits, _ = self.filter_processor.extract_user_subreddits(users, user_activities_map)

        logger.info("Checking subreddit similarity with AI provider...")
        similarity_map = await self.filter_processor.similarity_checker.check_similarity_batch(
            list(all_user_subreddits), matched_subreddits,
            batch_size=self.config.batch_size_default,
        )
        
        logger.info(
            f"Similarity check completed for {len(similarity_map)} subreddits"
        )
        
        relevant_subreddits = [
            subreddit for subreddit, is_relevant in similarity_map.items() 
            if is_relevant
        ]
        filtered_out_subreddits = [
            subreddit for subreddit, is_relevant in similarity_map.items() 
            if not is_relevant
        ]
        
        # Filter users and activities
        filtered_users, filtered_activities, stats = (
            await self.filter_processor.filter_users(
                users,
                user_activities_map,
                matched_subreddits,
                similarity_map,
                min_activities
            )
        )

        result_activities_s3_url = None
        try:
            if len(filtered_activities) > self.config.s3_storage_threshold:
                temp_id = str(uuid.uuid4())
                s3_key = f"temp/filtered-activities/{temp_id}.json"
                result_activities_s3_url = upload_json_to_s3(
                    s3_key, filtered_activities,
                    s3_client=self.storage_client.get_raw_client(),
                    s3_bucket=self.storage_client.get_bucket_name(),
                    s3_region=self.storage_client.get_region(),
                )
        except Exception as s3_err:
            logger.warning(f"Failed to store filtered activities in S3: {s3_err}")

        response: Dict[str, Any] = {
            "total_users_before": stats["total_users"],
            "total_users_after": len(filtered_users),
            "total_activities_before": len(fetched_activities),
            "total_activities_after": stats["total_activities_after"],
            "users_filtered_out": stats["users_filtered_out"],
            "users_passed": stats["users_passed"],
            "users": filtered_users,
            "completed_at": datetime.now().isoformat(),
            # Add subreddit similarity data
            "subreddit_similarity_data": {
                "matched_subreddits": matched_subreddits,
                "all_checked_subreddits": list(all_user_subreddits),
                "relevant_subreddits": relevant_subreddits,
                "filtered_out_subreddits": filtered_out_subreddits,
                "similarity_map": similarity_map,
                "total_checked_count": len(all_user_subreddits),
                "relevant_count": len(relevant_subreddits),
                "filtered_out_count": len(filtered_out_subreddits),
                "total_users_before": stats["total_users"],
                "total_users_after": len(filtered_users),
                "users_filtered_out": stats["users_filtered_out"],
                "users_passed": stats["users_passed"]
            }
        }
        if result_activities_s3_url:
            response["activities_s3_url"] = result_activities_s3_url
            response["activities"] = []
        else:
            response["activities"] = filtered_activities
        return response

    # ── Async job helpers ────────────────────────────────────────────────────

    def _s3_input_key(self, job_id: str) -> str:
        return f"temp/subreddit-filter-jobs/{job_id}/input.json"

    def _s3_state_key(self, job_id: str) -> str:
        return f"temp/subreddit-filter-jobs/{job_id}/state.json"

    def _s3_result_key(self, job_id: str) -> str:
        return f"temp/subreddit-filter-jobs/{job_id}/result.json"

    def _upload_json(self, s3_key: str, data: Any) -> str:
        return upload_json_to_s3(
            s3_key, data,
            s3_client=self.storage_client.get_raw_client(),
            s3_bucket=self.storage_client.get_bucket_name(),
            s3_region=self.storage_client.get_region(),
        )

    def _download_json(self, s3_key: str) -> Any:
        return fetch_json_from_s3(
            s3_key,
            s3_client=self.storage_client.get_raw_client(),
            s3_bucket=self.storage_client.get_bucket_name(),
        )

    def store_job_input(
        self,
        job_id: str,
        matched_subreddits: List[str],
        users: List[Dict[str, Any]],
        activities: Optional[List[Dict[str, Any]]],
        activities_s3_url: Optional[str],
        min_activities: int,
    ) -> str:
        """Store input data in S3. Returns S3 key."""
        s3_key = self._s3_input_key(job_id)
        payload: Dict[str, Any] = {
            "matched_subreddits": matched_subreddits,
            "users": users,
            "min_activities": min_activities,
        }
        if activities_s3_url:
            payload["activities_s3_url"] = activities_s3_url
        elif activities:
            payload["activities"] = activities
        self._upload_json(s3_key, payload)
        return s3_key

    # ── Chunk processing ─────────────────────────────────────────────────────

    async def process_job_chunk(
        self,
        job_id: str,
        start_subreddit_index: int,
        chunk_size: int,
        base_url: str,
        enterprise_name: Optional[str] = None,
    ) -> None:
        """Process one chunk of subreddit similarity checks."""
        start_subreddit_index = int(start_subreddit_index)
        chunk_size = int(chunk_size)

        try:
            input_data = self._download_json(self._s3_input_key(job_id))
            matched_subreddits = input_data["matched_subreddits"]

            if start_subreddit_index == 0:
                await self._process_first_chunk(
                    job_id, input_data, chunk_size, base_url, enterprise_name,
                )
            else:
                await self._process_subsequent_chunk(
                    job_id, matched_subreddits, start_subreddit_index,
                    chunk_size, base_url, enterprise_name,
                )
        except Exception as e:
            logger.error(f"[SubSimilarityJob {job_id}] Chunk failed: {e}", exc_info=True)
            JobStatusManager.fail_job(job_id, error=str(e), enterprise_name=enterprise_name)

    async def _process_first_chunk(
        self,
        job_id: str,
        input_data: Dict[str, Any],
        chunk_size: int,
        base_url: str,
        enterprise_name: Optional[str],
    ) -> None:
        """First chunk: load activities, extract subreddits, build initial state, process first batch."""
        JobStatusManager.mark_processing(job_id, enterprise_name)

        matched_subreddits = input_data["matched_subreddits"]
        users = input_data["users"]
        activities_s3_url = input_data.get("activities_s3_url")
        activities_inline = input_data.get("activities")
        min_activities = input_data.get("min_activities", DEFAULT_MIN_ACTIVITIES)

        fetched_activities = activities_inline or []
        if activities_s3_url:
            s3_key = extract_s3_key_from_url(activities_s3_url)
            if s3_key:
                fetched_activities = self._download_json(s3_key)
                logger.info(f"[SubSimilarityJob {job_id}] Fetched {len(fetched_activities)} activities from S3")
            else:
                raise ValueError("Invalid S3 URL for activities")

        if not fetched_activities:
            raise ValueError("No activities provided")

        user_activities_map = self.filter_processor.group_activities_by_username(fetched_activities)
        all_user_subreddits, _ = self.filter_processor.extract_user_subreddits(users, user_activities_map)

        normalized_matched = [normalize_subreddit_name(s) for s in matched_subreddits]
        matched_set = set(normalized_matched)
        similarity_map: Dict[str, bool] = {}
        subreddits_to_check: List[str] = []
        for sub in all_user_subreddits:
            if sub in matched_set:
                similarity_map[sub] = True
            else:
                subreddits_to_check.append(sub)

        logger.info(
            f"[SubSimilarityJob {job_id}] {len(similarity_map)} exact matches, "
            f"{len(subreddits_to_check)} subreddits to check with AI"
        )

        state = {
            "subreddits_to_check": subreddits_to_check,
            "similarity_map": similarity_map,
            "all_activities_count": len(fetched_activities),
        }
        self._upload_json(self._s3_state_key(job_id), state)

        JobStatusManager.update_totals(
            job_id,
            total_subreddits=len(subreddits_to_check),
            total_users_before=len(users),
            enterprise_name=enterprise_name,
        )

        if not subreddits_to_check:
            await self._finalize_job(job_id, enterprise_name)
            return

        batch = subreddits_to_check[:chunk_size]
        batch_results = await self.filter_processor.similarity_checker.check_similarity_batch(
            batch, normalized_matched, batch_size=self.config.batch_size_default,
        )
        similarity_map.update(batch_results)
        state["similarity_map"] = similarity_map
        self._upload_json(self._s3_state_key(job_id), state)

        checked_so_far = min(chunk_size, len(subreddits_to_check))
        JobStatusManager.update_progress(job_id, checked_so_far, enterprise_name)

        has_more = await ChunkOrchestrator.handle_chunk_completion(
            base_url, job_id, 0, chunk_size,
            len(subreddits_to_check), enterprise_name,
        )
        if not has_more:
            await self._finalize_job(job_id, enterprise_name)

    async def _process_subsequent_chunk(
        self,
        job_id: str,
        matched_subreddits: List[str],
        start_subreddit_index: int,
        chunk_size: int,
        base_url: str,
        enterprise_name: Optional[str],
    ) -> None:
        """Subsequent chunks: load state, check next batch, trigger next or finalize."""
        state = self._download_json(self._s3_state_key(job_id))
        subreddits_to_check: List[str] = state["subreddits_to_check"]
        similarity_map: Dict[str, bool] = state["similarity_map"]

        normalized_matched = [normalize_subreddit_name(s) for s in matched_subreddits]
        batch = subreddits_to_check[start_subreddit_index:start_subreddit_index + chunk_size]

        if batch:
            batch_results = await self.filter_processor.similarity_checker.check_similarity_batch(
                batch, normalized_matched, batch_size=self.config.batch_size_default,
            )
            similarity_map.update(batch_results)
            state["similarity_map"] = similarity_map
            self._upload_json(self._s3_state_key(job_id), state)

        checked_so_far = min(start_subreddit_index + chunk_size, len(subreddits_to_check))
        JobStatusManager.update_progress(job_id, checked_so_far, enterprise_name)

        has_more = await ChunkOrchestrator.handle_chunk_completion(
            base_url, job_id, start_subreddit_index, chunk_size,
            len(subreddits_to_check), enterprise_name,
        )
        if not has_more:
            await self._finalize_job(job_id, enterprise_name)

    async def _finalize_job(self, job_id: str, enterprise_name: Optional[str]) -> None:
        """All similarity checks done — filter users and complete the job."""
        input_data = self._download_json(self._s3_input_key(job_id))
        state = self._download_json(self._s3_state_key(job_id))

        matched_subreddits = input_data["matched_subreddits"]
        users = input_data["users"]
        activities_s3_url = input_data.get("activities_s3_url")
        activities_inline = input_data.get("activities")
        min_activities = input_data.get("min_activities", DEFAULT_MIN_ACTIVITIES)
        similarity_map: Dict[str, bool] = state["similarity_map"]
        subreddits_to_check: List[str] = state["subreddits_to_check"]

        fetched_activities = activities_inline or []
        if activities_s3_url:
            s3_key = extract_s3_key_from_url(activities_s3_url)
            if s3_key:
                fetched_activities = self._download_json(s3_key)

        user_activities_map = self.filter_processor.group_activities_by_username(fetched_activities)

        filtered_users, filtered_activities, stats = await self.filter_processor.filter_users(
            users, user_activities_map, matched_subreddits, similarity_map, min_activities,
        )

        result_activities_s3_url = None
        try:
            if len(filtered_activities) > self.config.s3_storage_threshold:
                temp_id = str(uuid.uuid4())
                filt_key = f"temp/filtered-activities/{temp_id}.json"
                result_activities_s3_url = self._upload_json(filt_key, filtered_activities)
        except Exception as s3_err:
            logger.warning(f"[SubSimilarityJob {job_id}] Failed to store filtered activities in S3: {s3_err}")

        result: Dict[str, Any] = {
            "total_users_before": stats["total_users"],
            "total_users_after": len(filtered_users),
            "total_activities_before": len(fetched_activities),
            "total_activities_after": stats["total_activities_after"],
            "users_filtered_out": stats["users_filtered_out"],
            "users_passed": stats["users_passed"],
            "completed_at": datetime.now().isoformat(),
        }
        if result_activities_s3_url:
            result["activities_s3_url"] = result_activities_s3_url

        full_result = {**result, "users": filtered_users}
        if not result_activities_s3_url:
            full_result["activities"] = filtered_activities
        else:
            full_result["activities"] = []

        result_s3_key = self._s3_result_key(job_id)
        self._upload_json(result_s3_key, full_result)

        JobStatusManager.complete_job(
            job_id=job_id,
            result=result,
            total_users_before=stats["total_users"],
            total_users_after=len(filtered_users),
            total_subreddits=len(subreddits_to_check),
            checked_subreddits=len(subreddits_to_check),
            result_s3_key=result_s3_key,
            enterprise_name=enterprise_name,
        )
        logger.info(
            f"[SubSimilarityJob {job_id}] Completed: {stats['users_passed']} users passed, "
            f"{stats['users_filtered_out']} filtered out"
        )
