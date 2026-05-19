"""
LinkedIn room pipeline run planning (skip completed steps on new workflow runs).

Vercel Workflow should call **POST** ``.../linkedin-room-pipeline/plan`` before executing steps,
then only invoke FastAPI routes for ``stepsToRun``. See ``docs/LINKEDIN_PIPELINE_SKIP_STEPS.md``.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field

from app import database
from app.services.linkedin_room_pipeline_async.pipeline_steps import (
    MAX_STEP,
    PHASE1_LAST_STEP,
    build_pipeline_run_plan,
    get_all_step_status,
)
from app.utils.helpers import ensure_db_available
from app.utils.s3_utils import get_audience_type_from_source

router = APIRouter()


class PipelineRunPlanBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    enterprise_name: Optional[str] = Field(
        default=None,
        alias="enterpriseName",
        description="Enterprise tenant (e.g. beta, gamma).",
    )
    start_from_step: int = Field(
        default=1,
        alias="startFromStep",
        ge=1,
        le=MAX_STEP,
        description="First step to consider (1=rebuild … 7=sgo). Steps below this are never run.",
    )
    skip_completed_steps: bool = Field(
        default=True,
        alias="skipCompletedSteps",
        description="When true, skip steps whose canonical S3 outputs already exist.",
    )
    run_sgo_pipeline: bool = Field(
        default=False,
        alias="runSgoPipeline",
        description="When false, plan through step 6 (initial prediction) only.",
    )
    force_steps: Optional[List[int]] = Field(
        default=None,
        alias="forceSteps",
        description="Step numbers to run even if already complete (e.g. [5] after fixing step-5 code).",
    )
    require_prerequisites: bool = Field(
        default=True,
        alias="requirePrerequisites",
        description="When true, steps before startFromStep must already be complete on S3.",
    )


def _ensure_linkedin_room(audience_room_id: str, enterprise_name: Optional[str]) -> None:
    ensure_db_available("audience")
    room = database.find_audience_room_by_id(
        audience_room_id, include_profiles=False, enterprise_name=enterprise_name
    )
    if not room:
        raise HTTPException(
            status_code=404, detail=f"Audience room {audience_room_id} not found"
        )
    if get_audience_type_from_source(room.source) != "linkedin-audience":
        raise HTTPException(
            status_code=400,
            detail="Pipeline planning is only supported for LinkedIn audience rooms.",
        )


@router.get("/api/v1/audience-rooms/{audience_room_id}/linkedin-room-pipeline/step-status")
async def linkedin_room_pipeline_step_status(
    audience_room_id: str = Path(..., description="Audience room ID"),
    enterpriseName: Optional[str] = Query(None, description="Enterprise tenant."),
) -> Dict[str, Any]:
    """S3-based completion status for pipeline steps 1–7 (for ops and workflow)."""
    _ensure_linkedin_room(audience_room_id, enterpriseName)
    room = database.find_audience_room_by_id(
        audience_room_id, include_profiles=False, enterprise_name=enterpriseName
    )
    steps = get_all_step_status(audience_room_id, enterpriseName, room.source if room else None)
    return {
        "audienceRoomId": audience_room_id,
        "enterpriseName": enterpriseName,
        "steps": steps,
    }


@router.post("/api/v1/audience-rooms/{audience_room_id}/linkedin-room-pipeline/plan")
async def linkedin_room_pipeline_plan(
    audience_room_id: str = Path(..., description="Audience room ID"),
    body: PipelineRunPlanBody = ...,
) -> Dict[str, Any]:
    """
    Plan which pipeline steps to run on a **new** orchestration run.

    Example after step 5 failed and code was fixed::

        {"enterpriseName":"beta","startFromStep":5,"skipCompletedSteps":true}

    Returns ``stepsToRun: [5, 6]`` and ``stepsSkipped: [1, 2, 3, 4]`` when artifacts exist.
    """
    _ensure_linkedin_room(audience_room_id, body.enterprise_name)
    try:
        plan = build_pipeline_run_plan(
            audience_room_id,
            body.enterprise_name,
            start_from_step=body.start_from_step,
            skip_completed_steps=body.skip_completed_steps,
            run_sgo_pipeline=body.run_sgo_pipeline,
            force_steps=body.force_steps,
            require_prerequisites=body.require_prerequisites,
        )
    except ValueError as e:
        msg = str(e)
        if msg == "not_linkedin_audience":
            raise HTTPException(status_code=400, detail=msg) from e
        raise HTTPException(status_code=400, detail=msg) from e

    out = plan.to_dict()
    out["hint"] = (
        "Workflow: only execute HTTP steps listed in stepsToRun (see "
        "docs/LINKEDIN_PIPELINE_SKIP_STEPS.md)."
    )
    return out


@router.post("/api/v1/audience-rooms/{audience_room_id}/linkedin-room-pipeline/plan/query")
async def linkedin_room_pipeline_plan_query(
    audience_room_id: str = Path(..., description="Audience room ID"),
    enterpriseName: Optional[str] = Query(None),
    startFromStep: int = Query(1, ge=1, le=MAX_STEP),
    skipCompletedSteps: bool = Query(True),
    runSgoPipeline: bool = Query(False),
    requirePrerequisites: bool = Query(True),
) -> Dict[str, Any]:
    """GET-friendly plan via query params (same logic as POST /plan)."""
    body = PipelineRunPlanBody(
        enterpriseName=enterpriseName,
        startFromStep=startFromStep,
        skipCompletedSteps=skipCompletedSteps,
        runSgoPipeline=runSgoPipeline,
        requirePrerequisites=requirePrerequisites,
    )
    return await linkedin_room_pipeline_plan(audience_room_id, body)
