"""Tests for LinkedIn pipeline step planning (skip completed on new runs)."""

from unittest.mock import MagicMock, patch

import pytest

from app.services.linkedin_room_pipeline_async.pipeline_steps import (
    PHASE1_LAST_STEP,
    build_pipeline_run_plan,
)


def _step_complete_map(complete_through: int) -> dict:
    return {s: s <= complete_through for s in range(1, 8)}


@patch("app.services.linkedin_room_pipeline_async.pipeline_steps.database")
@patch("app.services.linkedin_room_pipeline_async.pipeline_steps.is_step_complete")
def test_plan_skips_completed_when_start_from_1(mock_complete, mock_db):
    mock_db.find_audience_room_by_id.return_value = MagicMock(source="linkedin-audience")
    mock_complete.side_effect = lambda _r, _e, _s, step, **_: _step_complete_map(4)[step]

    plan = build_pipeline_run_plan(
        "room-1",
        "beta",
        start_from_step=1,
        skip_completed_steps=True,
        run_sgo_pipeline=False,
    )

    assert plan.steps_skipped == [1, 2, 3, 4]
    assert plan.steps_to_run == [5, 6]
    assert plan.pipeline_through_step == PHASE1_LAST_STEP


@patch("app.services.linkedin_room_pipeline_async.pipeline_steps.database")
@patch("app.services.linkedin_room_pipeline_async.pipeline_steps.is_step_complete")
def test_plan_start_from_5_after_fix(mock_complete, mock_db):
    mock_db.find_audience_room_by_id.return_value = MagicMock(source="linkedin-audience")
    mock_complete.side_effect = lambda _r, _e, _s, step, **_: _step_complete_map(4)[step]

    plan = build_pipeline_run_plan(
        "room-1",
        "beta",
        start_from_step=5,
        skip_completed_steps=True,
        run_sgo_pipeline=False,
    )

    assert plan.steps_skipped == []
    assert plan.steps_to_run == [5, 6]


@patch("app.services.linkedin_room_pipeline_async.pipeline_steps.database")
@patch("app.services.linkedin_room_pipeline_async.pipeline_steps.is_step_complete")
def test_plan_force_step_5_reruns_even_if_complete(mock_complete, mock_db):
    mock_db.find_audience_room_by_id.return_value = MagicMock(source="linkedin-audience")
    mock_complete.side_effect = lambda _r, _e, _s, step, **_: step <= 6

    plan = build_pipeline_run_plan(
        "room-1",
        "beta",
        start_from_step=5,
        skip_completed_steps=True,
        run_sgo_pipeline=False,
        force_steps=[5],
    )

    assert plan.steps_to_run == [5]
    assert 6 in plan.steps_skipped
    assert 5 not in plan.steps_skipped


@patch("app.services.linkedin_room_pipeline_async.pipeline_steps.database")
@patch("app.services.linkedin_room_pipeline_async.pipeline_steps.is_step_complete")
def test_plan_rejects_start_from_5_if_prereq_missing(mock_complete, mock_db):
    mock_db.find_audience_room_by_id.return_value = MagicMock(source="linkedin-audience")
    mock_complete.side_effect = lambda _r, _e, _s, step, **_: step <= 2

    with pytest.raises(ValueError, match="prerequisite"):
        build_pipeline_run_plan(
            "room-1",
            "beta",
            start_from_step=5,
            skip_completed_steps=True,
            require_prerequisites=True,
        )
