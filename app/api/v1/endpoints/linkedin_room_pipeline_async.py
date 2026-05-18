"""Async long-running jobs for LinkedIn theme, stimulus, ground-truth extraction, and initial prediction.

Chunked jobs use ``/async/process`` per invocation. When ``workflowOrchestrated=true``, Vercel Workflow
chains chunks (no FastAPI HTTP self-trigger). Legacy callers omit the flag for self-trigger behavior.
"""

from typing import Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, Body, HTTPException, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.config import logger
from app.services.linkedin_room_pipeline_async import (
    JOB_TYPE_GROUND_TRUTH,
    JOB_TYPE_INITIAL_PREDICTION,
    JOB_TYPE_LINKEDIN_SGO_PIPELINE,
    JOB_TYPE_STIMULUS,
    JOB_TYPE_THEME,
    request_handler,
)
from app.services.linkedin_room_pipeline_async.workflow_orchestration import (
    build_process_chunk_response,
)
from app.services.user_profile_summarization_service.utils import get_base_url

router = APIRouter()


class _GroundTruthTiersBody(BaseModel):
    """Optional JSON body for POST .../ground-truth-extraction/async — ``tiers`` here overrides the query param."""

    model_config = ConfigDict(extra="ignore")

    tiers: Optional[str] = Field(
        default=None,
        description="both | tier1 | tier2. When set, overrides the `tiers` query parameter.",
    )


async def _trigger_stimulus_process_endpoint(
    base_url: str,
    audience_room_id: str,
    job_id: str,
    enterprise_name: Optional[str],
    model: Optional[str],
) -> None:
    url = (
        f"{base_url.rstrip('/')}/api/v1/audience-rooms/{audience_room_id}"
        "/contextual-stimulus-categorization/async/process"
    )
    params = {"jobId": job_id}
    if enterprise_name is not None:
        params["enterpriseName"] = enterprise_name
    if model is not None:
        params["model"] = model
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, params=params)
            resp.raise_for_status()
        logger.info("[StimulusJob] Triggered process endpoint for job %s", job_id)
    except httpx.ReadTimeout:
        logger.info(
            "[StimulusJob] Process endpoint triggered for job %s (read timeout; chunk may still be running)",
            job_id,
        )
    except Exception as e:
        logger.error(
            "[StimulusJob] Failed to trigger process for job %s: %s",
            job_id,
            e,
            exc_info=True,
        )


async def _trigger_ground_truth_process_endpoint(
    base_url: str,
    audience_room_id: str,
    job_id: str,
    enterprise_name: Optional[str],
    model: Optional[str],
) -> None:
    url = (
        f"{base_url.rstrip('/')}/api/v1/audience-rooms/{audience_room_id}/"
        "ground-truth-extraction/async/process"
    )
    params = {"jobId": job_id}
    if enterprise_name is not None:
        params["enterpriseName"] = enterprise_name
    if model is not None:
        params["model"] = model
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, params=params)
            resp.raise_for_status()
        logger.info("[GroundTruthJob] Triggered process for job %s", job_id)
    except httpx.ReadTimeout:
        logger.info(
            "[GroundTruthJob] Process trigger read timeout (chunk may still run) job %s", job_id
        )
    except Exception as e:
        logger.error(
            "[GroundTruthJob] Failed to trigger process for job %s: %s",
            job_id,
            e,
            exc_info=True,
        )


async def _trigger_initial_prediction_process_endpoint(
    base_url: str,
    audience_room_id: str,
    job_id: str,
    enterprise_name: Optional[str],
    model: Optional[str],
) -> None:
    url = (
        f"{base_url.rstrip('/')}/api/v1/audience-rooms/{audience_room_id}/"
        "linkedin-initial-prediction/async/process"
    )
    params = {"jobId": job_id}
    if enterprise_name is not None:
        params["enterpriseName"] = enterprise_name
    if model is not None:
        params["model"] = model
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, params=params)
            resp.raise_for_status()
        logger.info("[InitialPredictionJob] Triggered process for job %s", job_id)
    except httpx.ReadTimeout:
        logger.info(
            "[InitialPredictionJob] Process trigger read timeout (chunk may still run) job %s",
            job_id,
        )
    except Exception as e:
        logger.error(
            "[InitialPredictionJob] Failed to trigger process for job %s: %s",
            job_id,
            e,
            exc_info=True,
        )


async def _trigger_linkedin_sgo_pipeline_process_endpoint(
    base_url: str,
    audience_room_id: str,
    job_id: str,
    enterprise_name: Optional[str],
    model: Optional[str],
) -> None:
    url = (
        f"{base_url.rstrip('/')}/api/v1/audience-rooms/{audience_room_id}/"
        "linkedin-sgo-pipeline/async/process"
    )
    params = {"jobId": job_id}
    if enterprise_name is not None:
        params["enterpriseName"] = enterprise_name
    if model is not None:
        params["model"] = model
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, params=params)
            resp.raise_for_status()
        logger.info("[LinkedInSGOPipelineJob] Triggered process for job %s", job_id)
    except httpx.ReadTimeout:
        logger.info(
            "[LinkedInSGOPipelineJob] Process trigger read timeout (chunk may still run) job %s",
            job_id,
        )
    except Exception as e:
        logger.error(
            "[LinkedInSGOPipelineJob] Failed to trigger process for job %s: %s",
            job_id,
            e,
            exc_info=True,
        )


@router.post("/api/v1/audience-rooms/{audience_room_id}/theme_category_discovery/async")
async def theme_category_discovery_async(
    background_tasks: BackgroundTasks,
    audience_room_id: str = Path(..., description="Audience room ID"),
    enterpriseName: Optional[str] = Query(
        None,
        description="Enterprise name (gamma, app, entelligence, beta). If omitted, default audience DB.",
    ),
    model: Optional[str] = Query(
        None,
        description="Override model id. Default: gpt-4o.",
    ),
) -> dict:
    """
    Queue the same work as **POST** ``.../theme_category_discovery`` and return immediately with ``job_id``.
    Poll **GET** ``/api/v1/audience-rooms/{id}/theme_category_discovery/async/status/{job_id}``
    (``enterpriseName`` in query if used on trigger).
    """
    try:
        out = request_handler.trigger_async_theme_category_discovery(
            audience_room_id=audience_room_id,
            enterprise_name=enterpriseName,
            model=model,
        )
        job_id = out["job_id"]
        background_tasks.add_task(
            request_handler.process_theme_job,
            job_id=job_id,
            audience_room_id=audience_room_id,
            enterprise_name=enterpriseName,
            model=model,
        )
        logger.info("=== LinkedIn theme taxonomy async job %s queued (room %s) ===", job_id, audience_room_id)
        return out
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error in theme_category_discovery async: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/v1/audience-rooms/{audience_room_id}/contextual-stimulus-categorization/async")
async def contextual_stimulus_categorization_async(
    background_tasks: BackgroundTasks,
    audience_room_id: str = Path(..., description="Audience room ID"),
    enterpriseName: Optional[str] = Query(
        None,
        description="Enterprise name. If omitted, default audience database.",
    ),
    model: Optional[str] = Query(
        None,
        description="Override model id. Default: gpt-4o.",
    ),
    tiers: str = Query(
        "both",
        description="Which tiers to run: both (default), tier1, or tier2.",
    ),
    workflowOrchestrated: bool = Query(
        False,
        description="When true, do not auto-trigger /async/process; Vercel Workflow chains chunks.",
    ),
    request: Request = None,
) -> dict:
    """
    Queue the same work as **POST** ``.../contextual-stimulus-categorization`` and return ``job_id``.
    Poll **GET** ``/api/v1/audience-rooms/{id}/contextual-stimulus-categorization/async/status/{job_id}``
    (``enterpriseName`` in query if used on trigger).
    """
    try:
        out = request_handler.trigger_async_stimulus_categorization(
            audience_room_id=audience_room_id,
            enterprise_name=enterpriseName,
            model=model,
            tiers=tiers,
        )
        job_id = out["job_id"]
        if not workflowOrchestrated:
            base_url = get_base_url()
            if request:
                try:
                    base_url = str(request.base_url).rstrip("/")
                except Exception:
                    pass
            background_tasks.add_task(
                _trigger_stimulus_process_endpoint,
                base_url,
                audience_room_id,
                job_id,
                enterpriseName,
                model,
            )
        logger.info(
            "=== LinkedIn stimulus async job %s queued (room %s) ===", job_id, audience_room_id
        )
        return out
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error in contextual-stimulus-categorization async: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post(
    "/api/v1/audience-rooms/{audience_room_id}/contextual-stimulus-categorization/async/process"
)
async def process_contextual_stimulus_categorization_async(
    audience_room_id: str = Path(..., description="Audience room ID"),
    jobId: str = Query(..., description="Job id returned from POST .../contextual-stimulus-categorization/async"),
    enterpriseName: Optional[str] = Query(None),
    model: Optional[str] = Query(None, description="Override model id. Default: gpt-4o."),
    workflowOrchestrated: bool = Query(
        False,
        description="When true, return needs_continue for workflow; do not self-trigger next chunk.",
    ),
    request: Request = None,
) -> dict:
    """Process one stimulus chunk. Legacy mode self-triggers the next chunk via HTTP."""
    base_url = get_base_url()
    if request:
        try:
            base_url = str(request.base_url).rstrip("/")
        except Exception:
            pass
    await request_handler.process_stimulus_job_chunk(
        job_id=jobId,
        audience_room_id=audience_room_id,
        enterprise_name=enterpriseName,
        model=model,
        base_url=base_url,
        workflow_orchestrated=workflowOrchestrated,
    )
    return build_process_chunk_response(
        job_id=jobId,
        enterprise_name=enterpriseName,
        workflow_orchestrated=workflowOrchestrated,
    )


@router.post("/api/v1/audience-rooms/{audience_room_id}/ground-truth-extraction/async")
async def ground_truth_extraction_async(
    background_tasks: BackgroundTasks,
    audience_room_id: str = Path(..., description="Audience room ID"),
    enterpriseName: Optional[str] = Query(
        None,
        description="Enterprise name. If omitted, default audience database.",
    ),
    model: Optional[str] = Query(
        None,
        description="Override model id; default from GROUND_TRUTH_EXTRACTION_CONFIG.",
    ),
    tiers: str = Query("both", description="both, tier1, or tier2."),
    workflowOrchestrated: bool = Query(
        False,
        description="When true, do not auto-trigger /async/process; Vercel Workflow chains chunks.",
    ),
    body: Optional[_GroundTruthTiersBody] = Body(default=None),
    request: Request = None,
) -> dict:
    """Standalone ground-truth extraction. Poll ground-truth-extraction/async/status/{job_id}.

    **Tiers:** use query ``?tiers=tier2`` **or** send JSON ``{"tiers": "tier2"}``. If the body
    sets ``tiers``, it overrides the query (so clients that only send a body are not stuck on
    the default ``both``).
    """
    try:
        effective_tiers = tiers
        if body is not None and body.tiers is not None and str(body.tiers).strip() != "":
            effective_tiers = str(body.tiers).strip().lower()
        out = request_handler.trigger_async_ground_truth_extraction(
            audience_room_id=audience_room_id,
            enterprise_name=enterpriseName,
            model=model,
            tiers=effective_tiers,
        )
        job_id = out["job_id"]
        if not workflowOrchestrated:
            base_url = get_base_url()
            if request:
                try:
                    base_url = str(request.base_url).rstrip("/")
                except Exception:
                    pass
            background_tasks.add_task(
                _trigger_ground_truth_process_endpoint,
                base_url,
                audience_room_id,
                job_id,
                enterpriseName,
                model,
            )
        logger.info(
            "=== LinkedIn ground-truth job %s queued (room %s) ===", job_id, audience_room_id
        )
        return out
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error in ground-truth-extraction async: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post(
    "/api/v1/audience-rooms/{audience_room_id}/ground-truth-extraction/async/process"
)
async def process_ground_truth_extraction_async(
    audience_room_id: str = Path(..., description="Audience room ID"),
    jobId: str = Query(
        ..., description="Job id from POST .../ground-truth-extraction/async"
    ),
    enterpriseName: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    workflowOrchestrated: bool = Query(
        False,
        description="When true, return needs_continue for workflow; do not self-trigger next chunk.",
    ),
    request: Request = None,
) -> dict:
    base_url = get_base_url()
    if request:
        try:
            base_url = str(request.base_url).rstrip("/")
        except Exception:
            pass
    await request_handler.process_ground_truth_extraction_job_chunk(
        job_id=jobId,
        audience_room_id=audience_room_id,
        enterprise_name=enterpriseName,
        model=model,
        base_url=base_url,
        workflow_orchestrated=workflowOrchestrated,
    )
    return build_process_chunk_response(
        job_id=jobId,
        enterprise_name=enterpriseName,
        workflow_orchestrated=workflowOrchestrated,
    )


@router.get(
    "/api/v1/audience-rooms/{audience_room_id}/ground-truth-extraction/async/status/{job_id}"
)
async def get_ground_truth_extraction_async_status(
    audience_room_id: str = Path(
        ..., description="Same room as the async ground-truth trigger"
    ),
    job_id: str = Path(
        ..., description="Job id returned from POST .../ground-truth-extraction/async"
    ),
    enterpriseName: Optional[str] = Query(
        None,
        description="Must match the database used when the job was created.",
    ),
) -> dict:
    try:
        return request_handler.get_job(
            job_id,
            enterprise_name=enterpriseName,
            audience_room_id=audience_room_id,
            expected_job_type=JOB_TYPE_GROUND_TRUTH,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error in ground-truth async status: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get(
    "/api/v1/audience-rooms/{audience_room_id}/theme_category_discovery/async/status/{job_id}"
)
async def get_theme_category_discovery_async_status(
    audience_room_id: str = Path(..., description="Same room as the async theme trigger"),
    job_id: str = Path(
        ..., description="Job id returned from POST .../theme_category_discovery/async"
    ),
    enterpriseName: Optional[str] = Query(
        None,
        description="Must match the database used when the job was created.",
    ),
) -> dict:
    """
    **status**: PENDING, PROCESSING, COMPLETED, FAILED. On **COMPLETED**, **result** is the same payload
    as synchronous ``POST .../theme_category_discovery`` would return.

    Path mirrors **comment-context-summary** async: ``{feature}/async/status/{job_id}``.
    """
    try:
        return request_handler.get_job(
            job_id,
            enterprise_name=enterpriseName,
            audience_room_id=audience_room_id,
            expected_job_type=JOB_TYPE_THEME,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error in theme_category_discovery async status: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get(
    "/api/v1/audience-rooms/{audience_room_id}/contextual-stimulus-categorization/async/status/{job_id}"
)
async def get_contextual_stimulus_categorization_async_status(
    audience_room_id: str = Path(
        ..., description="Same room as the async stimulus trigger"
    ),
    job_id: str = Path(
        ...,
        description="Job id returned from POST .../contextual-stimulus-categorization/async",
    ),
    enterpriseName: Optional[str] = Query(
        None,
        description="Must match the database used when the job was created.",
    ),
) -> dict:
    """
    **status**: PENDING, PROCESSING, COMPLETED, FAILED. On **COMPLETED**, **result** is the same payload
    as synchronous ``POST .../contextual-stimulus-categorization`` would return.
    """
    try:
        return request_handler.get_job(
            job_id,
            enterprise_name=enterpriseName,
            audience_room_id=audience_room_id,
            expected_job_type=JOB_TYPE_STIMULUS,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error in contextual-stimulus-categorization async status: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/v1/audience-rooms/{audience_room_id}/linkedin-initial-prediction/async")
async def linkedin_initial_prediction_async(
    background_tasks: BackgroundTasks,
    audience_room_id: str = Path(..., description="Audience room ID"),
    enterpriseName: Optional[str] = Query(
        None,
        description="Enterprise name. If omitted, default audience database.",
    ),
    model: Optional[str] = Query(
        None,
        description="Override OpenAI model id; default from INITIAL_PREDICTION_CONFIG.",
    ),
    tier: int = Query(
        1,
        ge=1,
        le=2,
        description="Tier 1 or 2 (must match ground-truth stimulus for that tier).",
    ),
    workflowOrchestrated: bool = Query(
        False,
        description="When true, do not auto-trigger /async/process; Vercel Workflow chains chunks.",
    ),
    request: Request = None,
) -> dict:
    """Queue chunked initial prediction; poll linkedin-initial-prediction/async/status/{job_id}."""
    try:
        out = request_handler.trigger_async_initial_prediction(
            audience_room_id=audience_room_id,
            enterprise_name=enterpriseName,
            model=model,
            tier=tier,
        )
        job_id = out["job_id"]
        if not workflowOrchestrated:
            base_url = get_base_url()
            if request:
                try:
                    base_url = str(request.base_url).rstrip("/")
                except Exception:
                    pass
            background_tasks.add_task(
                _trigger_initial_prediction_process_endpoint,
                base_url,
                audience_room_id,
                job_id,
                enterpriseName,
                model,
            )
        logger.info(
            "=== LinkedIn initial prediction async job %s queued (room %s) ===",
            job_id,
            audience_room_id,
        )
        return out
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error in linkedin-initial-prediction async: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post(
    "/api/v1/audience-rooms/{audience_room_id}/linkedin-initial-prediction/async/process"
)
async def process_linkedin_initial_prediction_async(
    audience_room_id: str = Path(..., description="Audience room ID"),
    jobId: str = Query(
        ..., description="Job id returned from POST .../linkedin-initial-prediction/async"
    ),
    enterpriseName: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    workflowOrchestrated: bool = Query(
        False,
        description="When true, return needs_continue for workflow; do not self-trigger next chunk.",
    ),
    request: Request = None,
) -> dict:
    base_url = get_base_url()
    if request:
        try:
            base_url = str(request.base_url).rstrip("/")
        except Exception:
            pass
    await request_handler.process_initial_prediction_job_chunk(
        job_id=jobId,
        audience_room_id=audience_room_id,
        enterprise_name=enterpriseName,
        model=model,
        base_url=base_url,
        workflow_orchestrated=workflowOrchestrated,
    )
    return build_process_chunk_response(
        job_id=jobId,
        enterprise_name=enterpriseName,
        workflow_orchestrated=workflowOrchestrated,
    )


@router.get(
    "/api/v1/audience-rooms/{audience_room_id}/linkedin-initial-prediction/async/status/{job_id}"
)
async def get_linkedin_initial_prediction_async_status(
    audience_room_id: str = Path(
        ..., description="Same room as the async initial-prediction trigger"
    ),
    job_id: str = Path(
        ..., description="Job id returned from POST .../linkedin-initial-prediction/async"
    ),
    enterpriseName: Optional[str] = Query(
        None,
        description="Must match the database used when the job was created.",
    ),
) -> dict:
    try:
        return request_handler.get_job(
            job_id,
            enterprise_name=enterpriseName,
            audience_room_id=audience_room_id,
            expected_job_type=JOB_TYPE_INITIAL_PREDICTION,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error in linkedin-initial-prediction async status: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/v1/audience-rooms/{audience_room_id}/linkedin-sgo-pipeline/async")
async def linkedin_sgo_pipeline_async(
    background_tasks: BackgroundTasks,
    audience_room_id: str = Path(..., description="Audience room ID"),
    enterpriseName: Optional[str] = Query(
        None,
        description="Enterprise name. If omitted, default audience database.",
    ),
    model: Optional[str] = Query(
        None,
        description="Override OpenAI model id for vendored delta-method LLM calls.",
    ),
    tierMode: str = Query(
        "tier1",
        description="tier1 or tier2 (tier2 seeds evolution baseline from tier1 SGO artifacts on S3).",
    ),
    numIterations: int = Query(
        5,
        ge=1,
        le=50,
        description="Planned outer-loop iterations (one HTTP chunk per iteration + final promote chunk).",
    ),
    workflowOrchestrated: bool = Query(
        False,
        description="When true, do not auto-trigger /async/process; Vercel Workflow chains chunks.",
    ),
    request: Request = None,
) -> dict:
    """Queue chunked LinkedIn SGO delta-method pipeline; poll linkedin-sgo-pipeline/async/status/{job_id}."""
    try:
        out = request_handler.trigger_async_linkedin_sgo_pipeline(
            audience_room_id=audience_room_id,
            enterprise_name=enterpriseName,
            model=model,
            tier_mode=tierMode,
            num_iterations=numIterations,
        )
        job_id = out["job_id"]
        if not workflowOrchestrated:
            base_url = get_base_url()
            if request:
                try:
                    base_url = str(request.base_url).rstrip("/")
                except Exception:
                    pass
            background_tasks.add_task(
                _trigger_linkedin_sgo_pipeline_process_endpoint,
                base_url,
                audience_room_id,
                job_id,
                enterpriseName,
                model,
            )
        logger.info(
            "=== LinkedIn SGO pipeline async job %s queued (room %s) ===",
            job_id,
            audience_room_id,
        )
        return out
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error in linkedin-sgo-pipeline async: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/v1/audience-rooms/{audience_room_id}/linkedin-sgo-pipeline/async/process")
async def process_linkedin_sgo_pipeline_async(
    audience_room_id: str = Path(..., description="Audience room ID"),
    jobId: str = Query(
        ..., description="Job id returned from POST .../linkedin-sgo-pipeline/async"
    ),
    enterpriseName: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    workflowOrchestrated: bool = Query(
        False,
        description="When true, return needs_continue for workflow; do not self-trigger next chunk.",
    ),
    request: Request = None,
) -> dict:
    base_url = get_base_url()
    if request:
        try:
            base_url = str(request.base_url).rstrip("/")
        except Exception:
            pass
    await request_handler.process_linkedin_sgo_pipeline_job_chunk(
        job_id=jobId,
        audience_room_id=audience_room_id,
        enterprise_name=enterpriseName,
        model=model,
        base_url=base_url,
        workflow_orchestrated=workflowOrchestrated,
    )
    return build_process_chunk_response(
        job_id=jobId,
        enterprise_name=enterpriseName,
        workflow_orchestrated=workflowOrchestrated,
    )


@router.get(
    "/api/v1/audience-rooms/{audience_room_id}/linkedin-sgo-pipeline/async/status/{job_id}"
)
async def get_linkedin_sgo_pipeline_async_status(
    audience_room_id: str = Path(
        ..., description="Same room as the async LinkedIn SGO pipeline trigger"
    ),
    job_id: str = Path(
        ..., description="Job id returned from POST .../linkedin-sgo-pipeline/async"
    ),
    enterpriseName: Optional[str] = Query(
        None,
        description="Must match the database used when the job was created.",
    ),
) -> dict:
    try:
        return request_handler.get_job(
            job_id,
            enterprise_name=enterpriseName,
            audience_room_id=audience_room_id,
            expected_job_type=JOB_TYPE_LINKEDIN_SGO_PIPELINE,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error in linkedin-sgo-pipeline async status: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e
