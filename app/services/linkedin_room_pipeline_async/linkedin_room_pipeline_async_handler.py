"""Async LinkedIn room pipeline jobs: theme taxonomy and stimulus (DB + background tasks)."""

import httpx
import uuid
from typing import Any, Dict, Optional

from fastapi import HTTPException

from app import database
from app.config import logger, s3_bucket, s3_client
from app.linkedin_tiered_s3 import get_linkedin_tiered_s3_artifact_names
from app.utils.s3_utils import get_s3_key_for_audience, try_fetch_json_from_s3, upload_json_to_s3
from app.services.linkedin_stimulus_extraction_service.stimulus_extraction_request_handler import (
    StimulusExtractionRequestHandler,
)
from app.services.linkedin_theme_discovery_service.linkedin_theme_discovery_request_handler import (
    LinkedinThemeDiscoveryRequestHandler,
)
from app.utils.helpers import ensure_db_available
from app.services.linkedin_stimulus_extraction_service.stimulus_data import (
    load_allowed_categories_from_mapping,
    load_tier1_flat,
    load_tier2_flat,
)
from app.services.linkedin_stimulus_extraction_service.stimulus_extraction_batch_processor import (
    run_tier1_stimulus_extraction_chunk,
    run_tier2_category_extraction_chunk,
)
from app.services.linkedin_stimulus_extraction_service.stimulus_extraction_config import (
    get_stimulus_extraction_config,
)
from .linkedin_room_pipeline_job_repository import (
    JOB_TYPE_GROUND_TRUTH,
    JOB_TYPE_INITIAL_PREDICTION,
    JOB_TYPE_LINKEDIN_SGO_PIPELINE,
    JOB_TYPE_STIMULUS,
    JOB_TYPE_THEME,
    create_linkedin_room_pipeline_job,
    get_linkedin_room_pipeline_job,
    update_linkedin_room_pipeline_job,
)

_THEME = LinkedinThemeDiscoveryRequestHandler()
_STIMULUS = StimulusExtractionRequestHandler()


def _detail_str(detail: Any) -> str:
    if detail is None:
        return "Unknown error"
    if isinstance(detail, str):
        return detail
    try:
        return str(detail)
    except Exception:
        return "Unknown error"


def _prevalidate_linkedin_pipeline_room(
    audience_room_id: str, enterprise_name: Optional[str] = None
) -> None:
    """S3 + room existence; not LinkedIn source is left to the worker (skipped result)."""
    ensure_db_available("audience")
    if not s3_client or not s3_bucket:
        raise HTTPException(
            status_code=503,
            detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.",
        )
    room = database.find_audience_room_by_id(
        audience_room_id, include_profiles=False, enterprise_name=enterprise_name
    )
    if not room:
        raise HTTPException(
            status_code=404, detail=f"Audience room {audience_room_id} not found"
        )


class LinkedInRoomPipelineAsyncHandler:
    """Create pipeline jobs, run theme/stimulus in background, poll status from DB."""

    def __init__(
        self,
        theme_handler: Optional[LinkedinThemeDiscoveryRequestHandler] = None,
        stimulus_handler: Optional[StimulusExtractionRequestHandler] = None,
    ):
        self._theme = theme_handler or _THEME
        self._stimulus = stimulus_handler or _STIMULUS

    def trigger_async_theme_category_discovery(
        self,
        audience_room_id: str,
        enterprise_name: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        _prevalidate_linkedin_pipeline_room(
            audience_room_id, enterprise_name=enterprise_name
        )
        job_id = str(uuid.uuid4())
        create_linkedin_room_pipeline_job(
            job_id=job_id,
            job_type=JOB_TYPE_THEME,
            audience_room_id=audience_room_id,
            model=model,
            enterprise_name=enterprise_name,
        )
        poll = (
            f"/api/v1/audience-rooms/{audience_room_id}/"
            f"theme_category_discovery/async/status/{job_id}"
        )
        return {
            "job_id": job_id,
            "job_type": JOB_TYPE_THEME,
            "status": "PENDING",
            "audience_room_id": audience_room_id,
            "poll": poll,
            "message": (
                f"Poll: GET {poll} (same enterpriseName in query if you set it on the trigger.)"
            ),
        }

    def trigger_async_stimulus_categorization(
        self,
        audience_room_id: str,
        enterprise_name: Optional[str] = None,
        model: Optional[str] = None,
        tiers: str = "both",
    ) -> Dict[str, Any]:
        _prevalidate_linkedin_pipeline_room(
            audience_room_id, enterprise_name=enterprise_name
        )
        tier_mode = (tiers or "both").strip().lower()
        if tier_mode not in ("both", "tier1", "tier2"):
            raise HTTPException(
                status_code=400,
                detail="Invalid tiers. Expected one of: both, tier1, tier2",
            )
        job_id = str(uuid.uuid4())
        create_linkedin_room_pipeline_job(
            job_id=job_id,
            job_type=JOB_TYPE_STIMULUS,
            audience_room_id=audience_room_id,
            model=model,
            enterprise_name=enterprise_name,
        )
        # Persist tier mode and starting tier cursor for chunk processor.
        update_linkedin_room_pipeline_job(
            job_id,
            enterprise_name=enterprise_name,
            current_tier=("tier2" if tier_mode == "tier2" else "tier1"),
            next_batch_index=0,
            result={"tier_mode": tier_mode},
        )
        poll = (
            f"/api/v1/audience-rooms/{audience_room_id}/"
            f"contextual-stimulus-categorization/async/status/{job_id}"
        )
        return {
            "job_id": job_id,
            "job_type": JOB_TYPE_STIMULUS,
            "status": "PENDING",
            "audience_room_id": audience_room_id,
            "poll": poll,
            "message": (
                f"Poll: GET {poll} (same enterpriseName in query if you set it on the trigger.)"
            ),
            "tiers": tier_mode,
        }

    def trigger_async_ground_truth_extraction(
        self,
        audience_room_id: str,
        enterprise_name: Optional[str] = None,
        model: Optional[str] = None,
        tiers: str = "both",
    ) -> Dict[str, Any]:
        _prevalidate_linkedin_pipeline_room(
            audience_room_id, enterprise_name=enterprise_name
        )
        tier_mode = (tiers or "both").strip().lower()
        if tier_mode not in ("both", "tier1", "tier2"):
            raise HTTPException(
                status_code=400,
                detail="Invalid tiers. Expected one of: both, tier1, tier2",
            )
        job_id = str(uuid.uuid4())
        create_linkedin_room_pipeline_job(
            job_id=job_id,
            job_type=JOB_TYPE_GROUND_TRUTH,
            audience_room_id=audience_room_id,
            model=model,
            enterprise_name=enterprise_name,
        )
        initial_tier = "tier2" if tier_mode == "tier2" else "tier1"
        update_linkedin_room_pipeline_job(
            job_id,
            enterprise_name=enterprise_name,
            current_tier=initial_tier,
            next_batch_index=0,
            result={"tier_mode": tier_mode},
        )
        poll = (
            f"/api/v1/audience-rooms/{audience_room_id}/"
            f"ground-truth-extraction/async/status/{job_id}"
        )
        return {
            "job_id": job_id,
            "job_type": JOB_TYPE_GROUND_TRUTH,
            "status": "PENDING",
            "audience_room_id": audience_room_id,
            "current_tier": initial_tier,
            "poll": poll,
            "message": (
                f"Mode {tier_mode} (active tier {initial_tier}). "
                f"Poll: GET {poll} (same enterpriseName in query if you set it on the trigger.)"
            ),
            "tiers": tier_mode,
        }

    def trigger_async_initial_prediction(
        self,
        audience_room_id: str,
        enterprise_name: Optional[str] = None,
        model: Optional[str] = None,
        tier: int = 1,
    ) -> Dict[str, Any]:
        _prevalidate_linkedin_pipeline_room(
            audience_room_id, enterprise_name=enterprise_name
        )
        tier_n = max(1, min(2, int(tier)))
        job_id = str(uuid.uuid4())
        create_linkedin_room_pipeline_job(
            job_id=job_id,
            job_type=JOB_TYPE_INITIAL_PREDICTION,
            audience_room_id=audience_room_id,
            model=model,
            enterprise_name=enterprise_name,
        )
        update_linkedin_room_pipeline_job(
            job_id,
            enterprise_name=enterprise_name,
            current_tier=("tier2" if tier_n == 2 else "tier1"),
            next_batch_index=0,
            result={"tier": tier_n},
        )
        poll = (
            f"/api/v1/audience-rooms/{audience_room_id}/"
            f"linkedin-initial-prediction/async/status/{job_id}"
        )
        return {
            "job_id": job_id,
            "job_type": JOB_TYPE_INITIAL_PREDICTION,
            "status": "PENDING",
            "audience_room_id": audience_room_id,
            "tier": tier_n,
            "poll": poll,
            "message": (
                f"Poll: GET {poll} (same enterpriseName in query if you set it on the trigger.)"
            ),
        }

    def trigger_async_linkedin_sgo_pipeline(
        self,
        audience_room_id: str,
        enterprise_name: Optional[str] = None,
        model: Optional[str] = None,
        *,
        tier_mode: str = "tier1",
        num_iterations: int = 5,
    ) -> Dict[str, Any]:
        """Chunked LinkedIn SGO delta-method pipeline (one outer iteration per worker invocation)."""
        _prevalidate_linkedin_pipeline_room(
            audience_room_id, enterprise_name=enterprise_name
        )
        tm = str(tier_mode or "tier1").strip().lower()
        if tm not in ("tier1", "tier2"):
            tm = "tier1"
        n_it = max(1, min(50, int(num_iterations)))
        job_id = str(uuid.uuid4())
        create_linkedin_room_pipeline_job(
            job_id=job_id,
            job_type=JOB_TYPE_LINKEDIN_SGO_PIPELINE,
            audience_room_id=audience_room_id,
            model=model,
            enterprise_name=enterprise_name,
        )
        tier_int = 2 if tm == "tier2" else 1
        update_linkedin_room_pipeline_job(
            job_id,
            enterprise_name=enterprise_name,
            current_tier=("tier2" if tier_int == 2 else "tier1"),
            total_items=0,
            completed_items=0,
            next_batch_index=0,
            result={
                "tier_mode": tm,
                "tier_int": tier_int,
                "num_iterations": n_it,
                "outer_iteration_next": 1,
            },
        )
        poll = (
            f"/api/v1/audience-rooms/{audience_room_id}/"
            f"linkedin-sgo-pipeline/async/status/{job_id}"
        )
        return {
            "job_id": job_id,
            "job_type": JOB_TYPE_LINKEDIN_SGO_PIPELINE,
            "status": "PENDING",
            "audience_room_id": audience_room_id,
            "tier_mode": tm,
            "num_iterations": n_it,
            "poll": poll,
            "message": (
                f"Poll: GET {poll}. Each chunk runs one outer iteration; checkpoints on S3."
            ),
        }

    async def process_linkedin_sgo_pipeline_job_chunk(
        self,
        *,
        job_id: str,
        audience_room_id: str,
        enterprise_name: Optional[str],
        model: Optional[str],
        base_url: str,
        workflow_orchestrated: bool = False,
    ) -> None:
        from app.services.linkedin_sgo_pipeline_service_async.linkedin_sgo_pipeline_async_worker import (
            run_linkedin_sgo_pipeline_job_chunk,
        )

        await run_linkedin_sgo_pipeline_job_chunk(
            job_id=job_id,
            audience_room_id=audience_room_id,
            enterprise_name=enterprise_name,
            model=model,
            base_url=base_url,
            workflow_orchestrated=workflow_orchestrated,
        )

    async def process_initial_prediction_job_chunk(
        self,
        *,
        job_id: str,
        audience_room_id: str,
        enterprise_name: Optional[str],
        model: Optional[str],
        base_url: str,
        workflow_orchestrated: bool = False,
    ) -> None:
        from app.services.linkedin_initial_prediction_service.initial_prediction_chunk_job import (
            run_initial_prediction_process_chunk,
        )

        await run_initial_prediction_process_chunk(
            job_id=job_id,
            audience_room_id=audience_room_id,
            enterprise_name=enterprise_name,
            model=model,
            base_url=base_url,
            workflow_orchestrated=workflow_orchestrated,
        )

    async def process_ground_truth_extraction_job_chunk(
        self,
        *,
        job_id: str,
        audience_room_id: str,
        enterprise_name: Optional[str],
        model: Optional[str],
        base_url: str,
        workflow_orchestrated: bool = False,
    ) -> None:
        from app.services.linkedin_ground_truth_extraction_service.ground_truth_extraction_chunk_job import (
            run_ground_truth_extraction_process_chunk,
        )

        await run_ground_truth_extraction_process_chunk(
            job_id=job_id,
            audience_room_id=audience_room_id,
            enterprise_name=enterprise_name,
            model=model,
            base_url=base_url,
            workflow_orchestrated=workflow_orchestrated,
        )

    async def process_theme_job(
        self,
        job_id: str,
        audience_room_id: str,
        enterprise_name: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        update_linkedin_room_pipeline_job(
            job_id, enterprise_name=enterprise_name, status="PROCESSING", error=None
        )
        try:
            out = await self._theme.theme_category_discovery(
                audience_room_id=audience_room_id,
                enterprise_name=enterprise_name,
                model=model,
            )
        except HTTPException as e:
            logger.error(
                "[LinkedInRoomPipelineJob %s] theme HTTPException: %s", job_id, e.detail
            )
            update_linkedin_room_pipeline_job(
                job_id,
                enterprise_name=enterprise_name,
                status="FAILED",
                error=_detail_str(e.detail),
            )
        except Exception as e:
            logger.error("[LinkedInRoomPipelineJob %s] theme failed: %s", job_id, e, exc_info=True)
            update_linkedin_room_pipeline_job(
                job_id,
                enterprise_name=enterprise_name,
                status="FAILED",
                error=str(e),
            )
        else:
            update_linkedin_room_pipeline_job(
                job_id,
                enterprise_name=enterprise_name,
                status="COMPLETED",
                result=out,
            )
            logger.info(
                "[LinkedInRoomPipelineJob %s] theme completed for room %s", job_id, audience_room_id
            )

    async def process_stimulus_job(
        self,
        job_id: str,
        audience_room_id: str,
        enterprise_name: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        update_linkedin_room_pipeline_job(
            job_id, enterprise_name=enterprise_name, status="PROCESSING", error=None
        )
        try:
            out = await self._stimulus.run_contextual_stimulus_categorization(
                audience_room_id=audience_room_id,
                enterprise_name=enterprise_name,
                model=model,
            )
        except HTTPException as e:
            logger.error(
                "[LinkedInRoomPipelineJob %s] stimulus HTTPException: %s", job_id, e.detail
            )
            update_linkedin_room_pipeline_job(
                job_id,
                enterprise_name=enterprise_name,
                status="FAILED",
                error=_detail_str(e.detail),
            )
        except Exception as e:
            logger.error(
                "[LinkedInRoomPipelineJob %s] stimulus failed: %s", job_id, e, exc_info=True
            )
            update_linkedin_room_pipeline_job(
                job_id,
                enterprise_name=enterprise_name,
                status="FAILED",
                error=str(e),
            )
        else:
            update_linkedin_room_pipeline_job(
                job_id,
                enterprise_name=enterprise_name,
                status="COMPLETED",
                result=out,
            )
            logger.info(
                "[LinkedInRoomPipelineJob %s] stimulus completed for room %s",
                job_id,
                audience_room_id,
            )

    async def process_stimulus_job_chunk(
        self,
        *,
        job_id: str,
        audience_room_id: str,
        enterprise_name: Optional[str],
        model: Optional[str],
        base_url: str,
        workflow_orchestrated: bool = False,
    ) -> None:
        """
        Chunked stimulus processing entrypoint (process endpoint).

        Tier-1 and tier-2 process up to N batches per invocation (see ``stimulus_extraction_config.yaml``),
        with bounded per-tier concurrency; S3 partial checkpoints and job progress are updated each chunk.
        """
        job = get_linkedin_room_pipeline_job(job_id, enterprise_name=enterprise_name)
        if not job:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
        if job.get("audience_room_id") != audience_room_id or job.get("job_type") != JOB_TYPE_STIMULUS:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
        if job["status"] in ("COMPLETED", "FAILED"):
            return
        job_result = job.get("result") or {}
        if not isinstance(job_result, dict):
            job_result = {}
        tier_mode = str(job_result.get("tier_mode") or "both").strip().lower()
        if tier_mode not in ("both", "tier1", "tier2"):
            tier_mode = "both"

        # Load room + S3 inputs
        room = database.find_audience_room_by_id(
            audience_room_id, include_profiles=False, enterprise_name=enterprise_name
        )
        if not room:
            update_linkedin_room_pipeline_job(
                job_id,
                enterprise_name=enterprise_name,
                status="FAILED",
                error=f"Audience room {audience_room_id} not found",
            )
            return

        art = get_linkedin_tiered_s3_artifact_names()
        base = get_s3_key_for_audience(
            audience_room_id, art.tiered_folder, enterprise_name, room.source
        ).rstrip("/")

        allowed_t1: list[str] = []
        if tier_mode in ("both", "tier1"):
            map_t1 = try_fetch_json_from_s3(
                f"{base}/{art.discovered_category_topic_mapping_tier1_filtered}"
            )
            if not map_t1 or not isinstance(map_t1, dict) or not map_t1:
                update_linkedin_room_pipeline_job(
                    job_id,
                    enterprise_name=enterprise_name,
                    status="FAILED",
                    error=f"No {art.discovered_category_topic_mapping_tier1_filtered!r} on S3. Run theme_category_discovery first.",
                )
                return
            allowed_t1 = load_allowed_categories_from_mapping(map_t1)

        t1_filt = try_fetch_json_from_s3(f"{base}/{art.tier_1_authored_filtered}")
        if not t1_filt or not isinstance(t1_filt, dict):
            update_linkedin_room_pipeline_job(
                job_id,
                enterprise_name=enterprise_name,
                status="FAILED",
                error=f"No {art.tier_1_authored_filtered!r} on S3. Run filter-tiered-posts first.",
            )
            return
        t2_filt = try_fetch_json_from_s3(
            f"{base}/{art.tier_2_reposts_and_comments_filtered}"
        )
        if not t2_filt or not isinstance(t2_filt, dict):
            update_linkedin_room_pipeline_job(
                job_id,
                enterprise_name=enterprise_name,
                status="FAILED",
                error=f"No {art.tier_2_reposts_and_comments_filtered!r} on S3. Run filter-tiered-posts first.",
            )
            return

        flat1 = load_tier1_flat(t1_filt)
        flat2 = load_tier2_flat(t2_filt)
        total_items = (
            len(flat1) + len(flat2)
            if tier_mode == "both"
            else (len(flat1) if tier_mode == "tier1" else len(flat2))
        )

        cfg = get_stimulus_extraction_config()
        bs = max(1, cfg.batch_size)
        bpc1 = cfg.tier1.batches_per_process_invocation
        bpc2 = cfg.tier2.batches_per_process_invocation
        mc1 = cfg.tier1.max_concurrency
        mc2 = cfg.tier2.max_concurrency
        sample_max = max(0, int(cfg.failed_post_ids_sample_max))

        # Establish defaults and progress bookkeeping
        mdl = model or (job.get("model") or cfg.default_model)
        current_tier = job.get("current_tier") or "tier1"
        next_batch_index = int(job.get("next_batch_index") or 0)
        update_linkedin_room_pipeline_job(
            job_id,
            enterprise_name=enterprise_name,
            status="PROCESSING",
            current_tier=current_tier,
            total_items=total_items,
            error=None,
        )

        # Partial checkpoint files (internal format)
        t1_partial_key = f"{base}/{art.contextual_stimulus_extraction_tier1_filtered}.partial"
        t2_partial_key = f"{base}/{art.contextual_stimulus_extraction_tier2_filtered}.partial"
        t1_partial = try_fetch_json_from_s3(t1_partial_key) or {}
        t2_partial = try_fetch_json_from_s3(t2_partial_key) or {}
        if not isinstance(t1_partial, dict):
            t1_partial = {}
        if not isinstance(t2_partial, dict):
            t2_partial = {}

        completed_items = int(job.get("completed_items") or 0)
        failed_items = int(job.get("failed_items") or 0)
        failed_sample = job.get("failed_post_ids_sample") or []
        if not isinstance(failed_sample, list):
            failed_sample = []

        if current_tier == "tier1":
            if tier_mode == "tier2":
                current_tier = "tier2"
                next_batch_index = 0
            if not flat1:
                current_tier = "tier2"
                next_batch_index = 0
            else:
                t1_total_batches = (len(flat1) + bs - 1) // bs
                start_b = next_batch_index
                end_b = min(t1_total_batches, start_b + bpc1)
                if start_b >= t1_total_batches:
                    current_tier = "tier2"
                    next_batch_index = 0
                else:
                    by_post, failures = await run_tier1_stimulus_extraction_chunk(
                        flat=flat1,
                        allowed_categories=allowed_t1,
                        model=mdl,
                        start_batch_index=start_b,
                        num_batches=bpc1,
                        max_concurrency=mc1,
                        batch_size=bs,
                    )
                    # Merge all rows in [start_b, end_b) batch range
                    users = t1_partial.setdefault("users", {})
                    row_start = start_b * bs
                    row_end = min(len(flat1), end_b * bs)
                    for user_id, post_id, body in flat1[row_start:row_end]:
                        u = users.setdefault(user_id, {"posts_by_id": {}})
                        if "posts_by_id" not in u:
                            u["posts_by_id"] = {}
                        row = by_post.get(post_id)
                        if row:
                            u["posts_by_id"][post_id] = {
                                "post_id": post_id,
                                "body_text": body,
                                "stimulus": row["stimulus"],
                                "category": row["category"],
                            }
                            completed_items += 1
                        else:
                            err = failures.get(post_id) or "tier1_failed"
                            t1_partial.setdefault("failures_by_post_id", {})[post_id] = err
                            failed_items += 1
                            if len(failed_sample) < sample_max:
                                failed_sample.append(post_id)
                    upload_json_to_s3(t1_partial_key, t1_partial)
                    next_batch_index = end_b
                    if next_batch_index >= t1_total_batches:
                        if tier_mode == "tier1":
                            current_tier = "done"
                        else:
                            current_tier = "tier2"
                            next_batch_index = 0

        if current_tier == "tier2":
            if not flat2:
                current_tier = "done"
            else:
                map_t2 = try_fetch_json_from_s3(
                    f"{base}/{art.discovered_category_topic_mapping_tier2_filtered}"
                )
                if map_t2 and isinstance(map_t2, dict) and map_t2.get("categories"):
                    try:
                        allowed_t2 = load_allowed_categories_from_mapping(map_t2)
                    except Exception:
                        allowed_t2 = allowed_t1
                else:
                    # Tier-2 may be run without tier-1 (tier_mode == "tier2"); in that case tier-2 mapping is required.
                    if tier_mode == "tier2":
                        update_linkedin_room_pipeline_job(
                            job_id,
                            enterprise_name=enterprise_name,
                            status="FAILED",
                            error=f"No {art.discovered_category_topic_mapping_tier2_filtered!r} on S3. Run theme_category_discovery (tier-2) first.",
                        )
                        return
                    allowed_t2 = allowed_t1

                t2_total_batches = (len(flat2) + bs - 1) // bs
                start_b = next_batch_index
                end_b = min(t2_total_batches, start_b + bpc2)
                if start_b >= t2_total_batches:
                    current_tier = "done"
                else:
                    by_post, failures = await run_tier2_category_extraction_chunk(
                        flat=flat2,
                        allowed_categories=allowed_t2,
                        model=mdl,
                        start_batch_index=start_b,
                        num_batches=bpc2,
                        max_concurrency=mc2,
                        batch_size=bs,
                    )
                    users = t2_partial.setdefault("users", {})
                    row_start = start_b * bs
                    row_end = min(len(flat2), end_b * bs)
                    for user_id, post_id, response, stimulus in flat2[row_start:row_end]:
                        u = users.setdefault(user_id, {"posts_by_id": {}})
                        if "posts_by_id" not in u:
                            u["posts_by_id"] = {}
                        cat = by_post.get(post_id)
                        if cat:
                            u["posts_by_id"][post_id] = {
                                "post_id": post_id,
                                "body_text": response,
                                "stimulus": stimulus,
                                "category": cat,
                            }
                            completed_items += 1
                        else:
                            err = failures.get(post_id) or "tier2_failed"
                            t2_partial.setdefault("failures_by_post_id", {})[post_id] = err
                            failed_items += 1
                            if len(failed_sample) < sample_max:
                                failed_sample.append(post_id)
                    upload_json_to_s3(t2_partial_key, t2_partial)
                    next_batch_index = end_b
                    if next_batch_index >= t2_total_batches:
                        current_tier = "done"

        update_linkedin_room_pipeline_job(
            job_id,
            enterprise_name=enterprise_name,
            current_tier=current_tier,
            next_batch_index=next_batch_index,
            completed_items=completed_items,
            failed_items=failed_items,
            failed_post_ids_sample=failed_sample,
        )

        if current_tier == "done":
            # Finalize to canonical artifact keys (convert internal posts_by_id -> posts list)
            result_payload: Dict[str, Any] = {
                "audience_room_id": audience_room_id,
                "status": "ok",
                "model": mdl,
                "completed_items": completed_items,
                "failed_items": failed_items,
                "tiers": tier_mode,
                "stimulus_extraction_config": cfg.to_public_dict(),
            }

            if tier_mode in ("both", "tier1"):
                t1_final = {"room_id": audience_room_id, "results_by_user": {}}
                for uid, bundle in (t1_partial.get("users") or {}).items():
                    posts_by_id = (bundle or {}).get("posts_by_id") or {}
                    t1_final["results_by_user"][uid] = {
                        "profile_info": (bundle or {}).get("profile_info") or {},
                        "posts": list(posts_by_id.values()),
                    }
                t1_key = f"{base}/{art.contextual_stimulus_extraction_tier1_filtered}"
                t1_url = upload_json_to_s3(t1_key, t1_final)
                result_payload["tier1"] = {"s3_key": t1_key, "s3_url": t1_url}

            if tier_mode in ("both", "tier2"):
                t2_final = {"room_id": audience_room_id, "results_by_user": {}}
                for uid, bundle in (t2_partial.get("users") or {}).items():
                    posts_by_id = (bundle or {}).get("posts_by_id") or {}
                    t2_final["results_by_user"][uid] = {
                        "profile_info": (bundle or {}).get("profile_info") or {},
                        "posts": list(posts_by_id.values()),
                    }
                t2_key = f"{base}/{art.contextual_stimulus_extraction_tier2_filtered}"
                t2_url = upload_json_to_s3(t2_key, t2_final)
                result_payload["tier2"] = {"s3_key": t2_key, "s3_url": t2_url}

            update_linkedin_room_pipeline_job(
                job_id,
                enterprise_name=enterprise_name,
                status="COMPLETED",
                current_tier="done",
                result=result_payload,
            )
            return

        from app.services.linkedin_room_pipeline_async.workflow_orchestration import (
            should_self_trigger_next_chunk,
        )

        if should_self_trigger_next_chunk(workflow_orchestrated=workflow_orchestrated):
            try:
                url = (
                    f"{base_url.rstrip('/')}/api/v1/audience-rooms/{audience_room_id}"
                    "/contextual-stimulus-categorization/async/process"
                )
                params: Dict[str, Any] = {"jobId": job_id}
                if enterprise_name is not None:
                    params["enterpriseName"] = enterprise_name
                if model is not None:
                    params["model"] = model
                async with httpx.AsyncClient(timeout=10.0) as client:
                    await client.post(url, params=params)
            except Exception:
                logger.info("[StimulusJob %s] Next chunk trigger attempted", job_id)

    def get_job(
        self,
        job_id: str,
        enterprise_name: Optional[str] = None,
        audience_room_id: Optional[str] = None,
        expected_job_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        job = get_linkedin_room_pipeline_job(job_id, enterprise_name=enterprise_name)
        if not job:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
        if audience_room_id is not None and job.get("audience_room_id") != audience_room_id:
            raise HTTPException(
                status_code=404, detail=f"Job {job_id} not found for this audience room"
            )
        if expected_job_type is not None and job.get("job_type") != expected_job_type:
            raise HTTPException(
                status_code=404, detail=f"Job {job_id} not found"
            )
        row: Dict[str, Any] = {
            "job_id": job["job_id"],
            "job_type": job["job_type"],
            "status": job["status"],
            "audience_room_id": job["audience_room_id"],
            "model": job["model"],
            "current_tier": job.get("current_tier"),
            "total_items": job.get("total_items"),
            "completed_items": job.get("completed_items"),
            "failed_items": job.get("failed_items"),
            "failed_post_ids_sample": job.get("failed_post_ids_sample"),
            "next_batch_index": job.get("next_batch_index"),
            "error": job["error"],
            "created_at": job["created_at"],
            "updated_at": job["updated_at"],
        }
        if job.get("result") is not None:
            row["result"] = job["result"]
        if job.get("job_type") == JOB_TYPE_GROUND_TRUTH:
            res = job.get("result")
            tm: Optional[str] = None
            if isinstance(res, dict):
                raw_tm = res.get("tier_mode")
                if raw_tm is not None and str(raw_tm).strip() != "":
                    tm = str(raw_tm).strip().lower()
            if tm:
                row["tiers"] = tm
                row["tier_mode"] = tm
            # `current_tier` is which S3 phase the worker is on (tier1 vs tier2); for `tiers=both`
            # it moves tier1 → tier2. `tiers` / `tier_mode` is the run mode you requested.
        if job.get("job_type") == JOB_TYPE_LINKEDIN_SGO_PIPELINE:
            res = job.get("result")
            if isinstance(res, dict):
                if res.get("outer_iteration_next") is not None:
                    row["outer_iteration_next"] = res.get("outer_iteration_next")
                if res.get("num_iterations") is not None:
                    row["num_iterations"] = res.get("num_iterations")
                if res.get("num_posts") is not None:
                    row["num_posts"] = res.get("num_posts")
                if res.get("sgo_resume_blocked") is not None:
                    row["sgo_resume_blocked"] = res.get("sgo_resume_blocked")
                if res.get("sgo_failure_class") is not None:
                    row["sgo_failure_class"] = res.get("sgo_failure_class")
                if res.get("heartbeat_at"):
                    row["heartbeat_at"] = res.get("heartbeat_at")
                if res.get("last_checkpoint_iteration") is not None:
                    row["last_checkpoint_iteration"] = res.get("last_checkpoint_iteration")
        return row


request_handler = LinkedInRoomPipelineAsyncHandler()
