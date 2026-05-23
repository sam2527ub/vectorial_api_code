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


def test_restore_checkpoint_writes_per_iteration_snapshot(tmp_path):
    from app.services.linkedin_sgo_pipeline_service_async.linkedin_sgo_pipeline_async_checkpoint import (
        _restore_prefix_into_workdir,
    )

    work_dir = tmp_path / "work"
    pred = {"predictions": [{"post_id": "p1"}]}
    resume = {
        "version": 1,
        "iteration_completed": 1,
        "tier_mode": "tier1",
        "qualifying_keys_for_next": [["uid1", "p1"]],
    }
    agg = {"deltas": [{"post_id": "p1", "delta": 0.3}]}

    def fake_json(key: str):
        if key.endswith("/resume.json"):
            return resume
        if key.endswith("/user_predictions.json"):
            return {"uid1": {"0": {"post_id": "p1"}}}
        if key.endswith("/delta_method_predictions.json"):
            return pred
        if key.endswith("/aggregate_deltas.json"):
            return agg
        return None

    with patch(
        "app.utils.s3_utils.try_fetch_json_from_s3",
        side_effect=fake_json,
    ), patch(
        "app.services.linkedin_sgo_pipeline_service_async.linkedin_sgo_pipeline_async_s3_workersafe.try_fetch_text_from_s3",
        return_value=None,
    ):
        doc, seed = _restore_prefix_into_workdir("rooms/x/checkpoints/job/after_iter_0001", work_dir)

    assert doc["iteration_completed"] == 1
    assert seed.is_file()
    assert (work_dir / "delta_method_predictions.json").is_file()
    iter_path = work_dir / "delta_method_predictions_iteration_1.json"
    assert iter_path.is_file()
    assert (work_dir / "linkedin_tier1_all_reviews_deltas.json").is_file()
