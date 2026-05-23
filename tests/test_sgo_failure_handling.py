"""Tests for SGO failure handling, checkpoints, stale jobs, and webhooks."""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.runtime_settings import reset_runtime_settings_cache
from app.services.linkedin_sgo_pipeline_service_async.linkedin_sgo_job_health import (
    is_sgo_job_stale,
    maybe_mark_stale_sgo_job_failed,
    reconcile_stale_sgo_job,
)
from app.services.linkedin_sgo_pipeline_service_async.linkedin_sgo_pipeline_async_checkpoint import (
    CHECKPOINT_VERSION,
    PARTIAL_CHECKPOINT_VERSION,
    build_partial_resume_payload,
    checkpoint_partial_prefix,
)


@pytest.fixture(autouse=True)
def _reset_settings():
    reset_runtime_settings_cache()
    yield
    reset_runtime_settings_cache()


def test_build_partial_resume_payload():
    doc = build_partial_resume_payload(
        outer_iteration=3,
        num_iterations_planned=5,
        tier_mode="tier1",
        progress_reason="after_batch_iter3_batch_2",
        processed_post_ids=["p1"],
        pred_snapshot_jsonable={"u": {}},
        failed_posts=[{"profile_id": "u1", "error": "x"}],
    )
    assert doc["version"] == PARTIAL_CHECKPOINT_VERSION
    assert doc["checkpoint_kind"] == "partial"
    assert doc["outer_iteration"] == 3


def test_checkpoint_partial_prefix_sanitizes_reason():
    root = "rooms/x/linkedin_sgo/checkpoints/job-1"
    p = checkpoint_partial_prefix(root, 2, "after/batch#1")
    assert "partials/after_batch_1" in p


def test_is_sgo_job_stale():
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    job = {"status": "PROCESSING", "result": {"heartbeat_at": old}}
    assert is_sgo_job_stale(job, stale_timeout_s=7200.0) is True
    fresh = {"status": "PROCESSING", "result": {"heartbeat_at": datetime.now(timezone.utc).isoformat()}}
    assert is_sgo_job_stale(fresh, stale_timeout_s=7200.0) is False


def test_reconcile_stale_sgo_job_meta():
    old = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    job = {"status": "PROCESSING", "result": {"heartbeat_at": old}}
    is_stale, meta = reconcile_stale_sgo_job(job, stale_timeout_s=60.0)
    assert is_stale is True
    assert meta is not None
    assert meta["sgo_failure_class"] == "stale_processing"
    assert meta["sgo_resume_blocked"] is False


def test_upload_iteration_checkpoint_commits_resume_last():
    from app.services.linkedin_sgo_pipeline_service_async import (
        linkedin_sgo_pipeline_async_checkpoint as ckpt,
    )

    upload_keys: list[str] = []

    def fake_upload_json(key: str, data: dict) -> str:
        upload_keys.append(key)
        return key

    with patch.object(ckpt, "_upload_checkpoint_artifact_files"):
        with patch.object(ckpt, "delete_partial_checkpoints_for_iteration"):
            with patch(
                "app.services.linkedin_sgo_pipeline_service_async.linkedin_sgo_pipeline_async_s3_workersafe.upload_json_to_s3_worker",
                side_effect=fake_upload_json,
            ):
                ckpt.upload_iteration_checkpoint(
                    base_tiered="rooms/r/tiered",
                    job_id="job-1",
                    iteration_completed=1,
                    resume_doc={"version": CHECKPOINT_VERSION},
                    user_pred_jsonable={},
                    work_dir=MagicMock(),
                    state_path=MagicMock(),
                    memory_path=MagicMock(),
                    trace_path=MagicMock(),
                    iteration_stats_path=MagicMock(),
                    out_path=MagicMock(),
                )
    assert upload_keys[-1].endswith("/resume.json")


def test_webhook_handler_accepts_tier_results_without_top_level_job_id():
    from app.services.sgo_fargate.webhook_handler import handle_sgo_fargate_webhook

    with patch(
        "app.services.sgo_fargate.webhook_handler.get_linkedin_room_pipeline_job",
        return_value={
            "job_type": "linkedin_sgo_pipeline",
            "status": "COMPLETED",
            "audience_room_id": "room-1",
            "result": {},
        },
    ):
        with patch(
            "app.services.sgo_fargate.webhook_handler._verify_webhook_secret",
            return_value=True,
        ):
            resp = asyncio.run(
                handle_sgo_fargate_webhook(
                    body={
                        "status": "COMPLETED",
                        "audience_room_id": "room-1",
                        "pipeline_mode": "tier1_then_tier2",
                        "tier_results": [
                            {"tier_mode": "tier1", "job_id": "job-t1", "status": "COMPLETED"},
                            {"tier_mode": "tier2", "job_id": "job-t2", "status": "COMPLETED"},
                        ],
                    },
                )
            )
    assert resp["job_id"] == "job-t1"
    assert resp["synced_job_ids"] == ["job-t1", "job-t2"]


def test_maybe_mark_stale_sgo_job_failed_updates_db():
    old = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    job = {"status": "PROCESSING", "result": {"heartbeat_at": old}}
    updated = {"status": "FAILED", "job_id": "j-stale"}

    with patch(
        "app.services.linkedin_room_pipeline_async.linkedin_room_pipeline_job_repository.update_linkedin_room_pipeline_job",
    ) as upd:
        with patch(
            "app.services.linkedin_room_pipeline_async.linkedin_room_pipeline_job_repository.get_linkedin_room_pipeline_job",
            return_value=updated,
        ):
            out = maybe_mark_stale_sgo_job_failed(
                job, job_id="j-stale", stale_timeout_s=60.0
            )
    assert upd.called
    assert out["status"] == "FAILED"


def test_runner_maps_system_exit_to_pipeline_error():
    from app.services.linkedin_sgo_pipeline_service_async.linkedin_sgo_pipeline_async_runner import (
        run_linkedin_sgo_pipeline_async,
    )
    from app.services.linkedin_sgo_pipeline_service_async.linkedin_sgo_pipeline_async_errors import (
        LinkedInSGOPipelineError,
    )

    ns = MagicMock()

    async def fake_run_all(_args):
        raise SystemExit(1)

    with patch(
        "app.services.linkedin_sgo_pipeline_service_async.linkedin_sgo_pipeline_async_runner.vendored_sgo_sys_path",
    ):
        with patch(
            "app.services.linkedin_sgo_pipeline_service_async.linkedin_sgo_pipeline_async_runner.ensure_required_paths",
        ):
            with patch("importlib.import_module") as imp:
                mod = MagicMock()
                mod.run_all = fake_run_all
                imp.return_value = mod
                with patch(
                    "app.services.linkedin_sgo_pipeline_service_async.linkedin_sgo_pipeline_async_runner.VENDORED_SGO_ROOT",
                    MagicMock(is_dir=lambda: True),
                ):
                    with patch.dict(sys.modules, {"_package_setup": MagicMock()}):
                        with pytest.raises(LinkedInSGOPipelineError, match="exited with status"):
                            asyncio.run(run_linkedin_sgo_pipeline_async(ns))
