"""SGO on AWS Fargate: start ECS worker + webhook callback (Apify-style external compute)."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.config import logger
from app.services.linkedin_room_pipeline_async import request_handler
from app.services.sgo_fargate import (
    SgoFargateLaunchError,
    aggregate_fargate_pipeline_status,
    fargate_is_configured,
    handle_sgo_fargate_webhook,
    start_linkedin_sgo_on_fargate,
)
from app.services.sgo_fargate.fargate_launcher import build_fargate_trigger_result
from app.services.user_profile_summarization_service.utils import get_base_url

router = APIRouter()


class SgoFargateStartBody(BaseModel):
    """Optional body for Fargate SGO start (workflow integration)."""

    model_config = ConfigDict(extra="ignore")

    workflowResumeUrl: Optional[str] = Field(
        default=None,
        description="URL for Vercel Workflow to resume when SGO completes (POST JSON callback).",
    )


class SgoFargateWebhookBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    job_id: Optional[str] = None
    status: str
    message: Optional[str] = None
    error: Optional[str] = None
    audience_room_id: Optional[str] = None
    tier_results: Optional[list] = None
    failed_tier: Optional[str] = None
    pipeline_mode: Optional[str] = None


@router.post("/api/v1/audience-rooms/{audience_room_id}/linkedin-sgo-pipeline/fargate/start")
async def linkedin_sgo_fargate_start(
    audience_room_id: str = Path(..., description="Audience room ID"),
    enterpriseName: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    tierMode: str = Query(
        "both",
        description="tier1 | tier2 | both (default: tier1 then tier2 on Fargate).",
    ),
    numIterations: int = Query(5, ge=1, le=50),
    notifyWebhook: bool = Query(
        False,
        description="When true, POST completion to /api/v1/sgo/fargate/webhook. Default false (poll-only).",
    ),
    body: Optional[SgoFargateStartBody] = None,
    request: Request = None,
) -> dict:
    """
    Create a LinkedIn SGO job and start AWS Fargate (returns immediately).

    Same job model as ``POST .../linkedin-sgo-pipeline/async`` but compute runs on ECS.
    Poll ``GET .../linkedin-sgo-pipeline/async/status/{job_id}`` or
    ``GET .../fargate/pipeline-status`` (tierMode=both).
    """
    if not fargate_is_configured():
        raise HTTPException(
            status_code=503,
            detail=(
                "SGO Fargate is not enabled. Set sgo_fargate.enabled and ECS settings in "
                "config/runtime.yaml or SGO_FARGATE_* environment variables."
            ),
        )

    try:
        tm = (tierMode or "both").strip().lower()
        if tm in ("both", "all", "tier1_tier2", "tier1+tier2"):
            out = build_fargate_trigger_result(
                audience_room_id=audience_room_id,
                enterprise_name=enterpriseName,
                model=model,
                tier_mode=tm,
                num_iterations=numIterations,
            )
        else:
            out = request_handler.trigger_async_linkedin_sgo_pipeline(
                audience_room_id=audience_room_id,
                enterprise_name=enterpriseName,
                model=model,
                tier_mode=tm,
                num_iterations=numIterations,
            )
        base_url = get_base_url()
        if request:
            try:
                base_url = str(request.base_url).rstrip("/")
            except Exception:
                pass
        resume_url = body.workflowResumeUrl if body else None
        return start_linkedin_sgo_on_fargate(
            trigger_result=out,
            audience_room_id=audience_room_id,
            enterprise_name=enterpriseName,
            model=model,
            base_url=base_url,
            workflow_resume_url=resume_url,
            tier_mode=tierMode,
            num_iterations=numIterations,
            notify_webhook=notifyWebhook,
        )
    except SgoFargateLaunchError as e:
        logger.error("Fargate launch failed for room %s: %s", audience_room_id, e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception as e:
        logger.error("linkedin-sgo fargate start: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get(
    "/api/v1/audience-rooms/{audience_room_id}/linkedin-sgo-pipeline/fargate/pipeline-status"
)
async def linkedin_sgo_fargate_pipeline_status(
    audience_room_id: str = Path(..., description="Audience room ID"),
    tier1JobId: str = Query(..., description="Tier1 job id from fargate/start tier_results"),
    tier2JobId: Optional[str] = Query(None, description="Tier2 job id (tierMode=both)"),
    pipelineRunId: Optional[str] = Query(None, description="Optional pipeline_run_id from start response"),
    enterpriseName: Optional[str] = Query(None),
) -> dict:
    """
    Poll-only rollup for tier1→tier2 Fargate runs.

    ``pipeline_status`` is ``COMPLETED`` only when all tier jobs are ``COMPLETED``.
    """
    return aggregate_fargate_pipeline_status(
        audience_room_id=audience_room_id,
        tier1_job_id=tier1JobId,
        tier2_job_id=tier2JobId,
        enterprise_name=enterpriseName,
        pipeline_run_id=pipelineRunId,
    )


@router.post("/api/v1/sgo/fargate/webhook")
async def sgo_fargate_webhook(
    payload: SgoFargateWebhookBody,
    x_sgo_webhook_secret: Optional[str] = Header(None, alias="X-SGO-Webhook-Secret"),
) -> dict:
    """Callback from ``run_sgo_worker.py`` when Fargate training finishes."""
    return await handle_sgo_fargate_webhook(
        body=payload.model_dump(),
        webhook_secret=x_sgo_webhook_secret,
    )
