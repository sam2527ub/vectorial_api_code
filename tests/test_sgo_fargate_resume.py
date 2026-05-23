"""Tests for SGO Fargate resume from S3 checkpoints."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.linkedin_sgo_pipeline_service_async.sgo_job_resume import (
    assess_sgo_job_resume,
    prepare_job_for_resume,
)


def test_assess_sgo_job_resume_allows_partial_checkpoint():
    job = {
        "job_id": "job-partial",
        "status": "FAILED",
        "result": {
            "num_iterations": 5,
            "outer_iteration_next": 1,
            "sgo_resume_blocked": True,
            "sgo_failure_class": "unknown",
        },
    }
    ckpt = {
        "completed_outer_iterations": 0,
        "outer_iteration_next": 1,
        "partial_checkpoint": {"progress_reason": "after_batch_iteration_1_batch_1"},
        "has_resume_checkpoint": True,
    }
    with patch(
        "app.services.linkedin_sgo_pipeline_service_async.sgo_job_resume.sgo_checkpoint_resume_state",
        return_value=ckpt,
    ):
        ok, reason, state = assess_sgo_job_resume(
            job, base_tiered="rooms/x/tiered", force=True
        )
    assert ok is True
    assert reason == "ok"
    assert state["partial_checkpoint"]["progress_reason"] == "after_batch_iteration_1_batch_1"


def test_assess_sgo_job_resume_blocked_without_force():
    job = {
        "job_id": "job-blocked",
        "status": "FAILED",
        "result": {
            "num_iterations": 5,
            "outer_iteration_next": 1,
            "sgo_resume_blocked": True,
        },
    }
    ckpt = {
        "completed_outer_iterations": 0,
        "outer_iteration_next": 1,
        "partial_checkpoint": {"progress_reason": "after_batch"},
        "has_resume_checkpoint": True,
    }
    with patch(
        "app.services.linkedin_sgo_pipeline_service_async.sgo_job_resume.sgo_checkpoint_resume_state",
        return_value=ckpt,
    ):
        ok, reason, _state = assess_sgo_job_resume(
            job, base_tiered="rooms/x/tiered", force=False
        )
    assert ok is False
    assert reason == "resume_blocked"


def test_prepare_job_for_resume_clears_blocked_flag():
    job = {
        "result": {
            "outer_iteration_next": 1,
            "sgo_resume_blocked": True,
        }
    }
    prepared = prepare_job_for_resume(
        job,
        checkpoint_state={"outer_iteration_next": 1},
        extra_result={"compute_backend": "fargate"},
    )
    assert prepared["sgo_resume_blocked"] is False
    assert prepared["sgo_force_resume"] is True
    assert prepared["compute_backend"] == "fargate"


def test_attribute_error_failure_is_resumable():
    from app.services.linkedin_sgo_pipeline_service_async.linkedin_sgo_pipeline_async_worker import (
        _sgo_failed_result_meta,
    )

    meta = _sgo_failed_result_meta({}, AttributeError("group_summary_max_words"))
    assert meta["sgo_failure_class"] == "config"
    assert meta["sgo_resume_blocked"] is False
