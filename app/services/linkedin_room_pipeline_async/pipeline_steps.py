"""
LinkedIn room pipeline step completion (S3 artifacts) and run planning.

Used when starting a **new** workflow run after a mid-pipeline failure: skip steps 1..N-1
when canonical outputs already exist on S3 (``skipCompletedSteps`` / ``startFromStep``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Set

from app import database
from app.config import s3_bucket, s3_client
from app.linkedin_tiered_s3 import LinkedinTieredS3ArtifactNames, get_linkedin_tiered_s3_artifact_names
from app.utils.s3_utils import (
    get_audience_type_from_source,
    get_s3_key_for_audience,
    s3_object_exists,
    try_fetch_json_from_s3,
)


def _non_empty_dict(data: Optional[Dict[str, Any]]) -> bool:
    return isinstance(data, dict) and len(data) > 0


def _has_users_or_results(data: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(data, dict):
        return False
    users = data.get("users")
    if isinstance(users, dict) and len(users) > 0:
        return True
    rb = data.get("results_by_user")
    if isinstance(rb, dict) and len(rb) > 0:
        return True
    return False


def _has_theme_categories(data: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(data, dict):
        return False
    cats = data.get("categories")
    if isinstance(cats, dict) and len(cats) > 0:
        return True
    if isinstance(cats, list) and len(cats) > 0:
        return True
    return False


@dataclass(frozen=True)
class PipelineStepDef:
    """One Phase-1 pipeline step (matches Vercel Workflow ordering)."""

    step: int
    step_id: str
    label: str
    artifact_basename: str
    validate_json: Optional[Callable[[Optional[Dict[str, Any]]], bool]] = None


# Phase 1: rebuild → filter → theme → stimulus → ground truth → initial prediction → SGO
PIPELINE_STEPS: tuple[PipelineStepDef, ...] = (
    PipelineStepDef(1, "rebuild", "Rebuild tiered posts", "manifest", None),
    PipelineStepDef(
        2,
        "filter",
        "Filter tiered posts",
        "tier_1_authored_filtered",
        _has_users_or_results,
    ),
    PipelineStepDef(
        3,
        "theme",
        "Theme category discovery",
        "discovered_category_topic_mapping_tier1_filtered",
        _has_theme_categories,
    ),
    PipelineStepDef(
        4,
        "stimulus",
        "Contextual stimulus categorization",
        "contextual_stimulus_extraction_tier1_filtered",
        _has_users_or_results,
    ),
    PipelineStepDef(
        5,
        "ground_truth",
        "Ground truth extraction",
        "ground_truth_extraction_tier1_filtered",
        _non_empty_dict,
    ),
    PipelineStepDef(
        6,
        "initial_prediction",
        "LinkedIn initial prediction",
        "initial_prediction_tier1",
        _non_empty_dict,
    ),
    PipelineStepDef(
        7,
        "sgo",
        "LinkedIn SGO delta-method pipeline",
        "linkedin_sgo_predictions_tier1",
        _non_empty_dict,
    ),
)

STEP_BY_NUMBER: Dict[int, PipelineStepDef] = {s.step: s for s in PIPELINE_STEPS}
MAX_STEP = max(STEP_BY_NUMBER)
PHASE1_LAST_STEP = 6


def _artifact_basename_for_step(
    step_def: PipelineStepDef, art: LinkedinTieredS3ArtifactNames
) -> str:
    return str(getattr(art, step_def.artifact_basename))


def tiered_base_key(
    audience_room_id: str,
    enterprise_name: Optional[str],
    source: Optional[str],
    art: Optional[LinkedinTieredS3ArtifactNames] = None,
) -> str:
    art = art or get_linkedin_tiered_s3_artifact_names()
    return get_s3_key_for_audience(
        audience_room_id, art.tiered_folder, enterprise_name, source
    ).rstrip("/")


def step_s3_key(
    audience_room_id: str,
    enterprise_name: Optional[str],
    source: Optional[str],
    step_def: PipelineStepDef,
    art: Optional[LinkedinTieredS3ArtifactNames] = None,
) -> str:
    art = art or get_linkedin_tiered_s3_artifact_names()
    base = tiered_base_key(audience_room_id, enterprise_name, source, art)
    name = _artifact_basename_for_step(step_def, art)
    return f"{base}/{name}"


def is_step_complete(
    audience_room_id: str,
    enterprise_name: Optional[str],
    source: Optional[str],
    step: int,
    *,
    art: Optional[LinkedinTieredS3ArtifactNames] = None,
) -> bool:
    """True when the step's canonical S3 artifact exists and passes validation."""
    step_def = STEP_BY_NUMBER.get(step)
    if not step_def:
        return False

    art = art or get_linkedin_tiered_s3_artifact_names()
    key = step_s3_key(audience_room_id, enterprise_name, source, step_def, art)

    if step == 1:
        # Rebuild: manifest + unfiltered tier_1 file
        manifest_key = f"{tiered_base_key(audience_room_id, enterprise_name, source, art)}/{art.manifest}"
        t1_key = f"{tiered_base_key(audience_room_id, enterprise_name, source, art)}/{art.tier_1_authored}"
        if not s3_object_exists(manifest_key) or not s3_object_exists(t1_key):
            return False
        manifest = try_fetch_json_from_s3(manifest_key)
        return _non_empty_dict(manifest)

    if not s3_object_exists(key):
        return False
    if step_def.validate_json is None:
        return True
    payload = try_fetch_json_from_s3(key)
    return bool(step_def.validate_json(payload))


def get_all_step_status(
    audience_room_id: str,
    enterprise_name: Optional[str],
    source: Optional[str],
) -> List[Dict[str, Any]]:
    art = get_linkedin_tiered_s3_artifact_names()
    out: List[Dict[str, Any]] = []
    for step_def in PIPELINE_STEPS:
        complete = is_step_complete(
            audience_room_id, enterprise_name, source, step_def.step, art=art
        )
        out.append(
            {
                "step": step_def.step,
                "stepId": step_def.step_id,
                "label": step_def.label,
                "complete": complete,
                "s3Key": step_s3_key(audience_room_id, enterprise_name, source, step_def, art),
            }
        )
    return out


@dataclass
class PipelineRunPlan:
    audience_room_id: str
    enterprise_name: Optional[str]
    start_from_step: int
    skip_completed_steps: bool
    run_sgo_pipeline: bool
    pipeline_through_step: int
    steps_to_run: List[int]
    steps_skipped: List[int]
    step_status: List[Dict[str, Any]]
    force_steps: List[int]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "audienceRoomId": self.audience_room_id,
            "enterpriseName": self.enterprise_name,
            "startFromStep": self.start_from_step,
            "skipCompletedSteps": self.skip_completed_steps,
            "runSgoPipeline": self.run_sgo_pipeline,
            "pipelineThrough": STEP_BY_NUMBER[self.pipeline_through_step].step_id,
            "pipelineThroughStep": self.pipeline_through_step,
            "stepsToRun": self.steps_to_run,
            "stepsSkipped": self.steps_skipped,
            "forceSteps": self.force_steps,
            "stepStatus": self.step_status,
        }


def build_pipeline_run_plan(
    audience_room_id: str,
    enterprise_name: Optional[str],
    *,
    start_from_step: int = 1,
    skip_completed_steps: bool = True,
    run_sgo_pipeline: bool = False,
    force_steps: Optional[Sequence[int]] = None,
    require_prerequisites: bool = True,
) -> PipelineRunPlan:
    """
  Build which pipeline steps to execute on a new orchestration run.

  - ``startFromStep``: minimum step index (1–7); steps below this are never run.
  - ``skipCompletedSteps``: when True, skip steps whose canonical S3 artifacts exist.
  - ``forceSteps``: always run these step numbers even if complete (re-run after code fix).
  - ``require_prerequisites``: when True, steps before ``startFromStep`` must already be complete.
    """
    start = max(1, min(int(start_from_step), MAX_STEP))
    through = MAX_STEP if run_sgo_pipeline else PHASE1_LAST_STEP
    force_set: Set[int] = {int(s) for s in (force_steps or []) if int(s) in STEP_BY_NUMBER}

    room = database.find_audience_room_by_id(
        audience_room_id, include_profiles=False, enterprise_name=enterprise_name
    )
    source = room.source if room else None
    if room and get_audience_type_from_source(room.source) != "linkedin-audience":
        raise ValueError("not_linkedin_audience")

    step_status = get_all_step_status(audience_room_id, enterprise_name, source)
    by_step = {row["step"]: row for row in step_status}

    if require_prerequisites and start > 1:
        missing = [
            s
            for s in range(1, start)
            if not by_step.get(s, {}).get("complete")
        ]
        if missing:
            labels = [STEP_BY_NUMBER[s].step_id for s in missing]
            raise ValueError(
                f"Cannot startFromStep={start}: prerequisite step(s) not complete on S3: "
                f"{', '.join(labels)}. Run those steps first or lower startFromStep."
            )

    steps_to_run: List[int] = []
    steps_skipped: List[int] = []

    for step in range(start, through + 1):
        complete = bool(by_step.get(step, {}).get("complete"))
        if step in force_set:
            steps_to_run.append(step)
        elif skip_completed_steps and complete:
            steps_skipped.append(step)
        else:
            steps_to_run.append(step)

    return PipelineRunPlan(
        audience_room_id=audience_room_id,
        enterprise_name=enterprise_name,
        start_from_step=start,
        skip_completed_steps=skip_completed_steps,
        run_sgo_pipeline=run_sgo_pipeline,
        pipeline_through_step=through,
        steps_to_run=steps_to_run,
        steps_skipped=steps_skipped,
        step_status=step_status,
        force_steps=sorted(force_set),
    )


def resolve_room_source_for_plan(
    audience_room_id: str, enterprise_name: Optional[str]
) -> Optional[str]:
    room = database.find_audience_room_by_id(
        audience_room_id, include_profiles=False, enterprise_name=enterprise_name
    )
    return room.source if room else None
