"""Tests for LinkedIn SGO async checkpoints, worker finalize path, and route wiring.

Avoid importing ``linkedin_room_pipeline_job_repository`` / ``LinkedInRoomPipelineAsyncHandler`` at
module level: those chains pull the full DB client at import time.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

JOB_TYPE_LINKEDIN_SGO_PIPELINE = "linkedin_sgo_pipeline"


def _install_sys_module(name: str, mod: object) -> object | None:
    prev = sys.modules.get(name)
    sys.modules[name] = mod
    return prev


def _restore_sys_module(name: str, prev: object | None) -> None:
    if prev is None:
        sys.modules.pop(name, None)
    else:
        sys.modules[name] = prev


def test_latest_completed_checkpoint_iteration_scan():
    from unittest.mock import patch

    from app.services.linkedin_sgo_pipeline_service_async.linkedin_sgo_pipeline_async_checkpoint import (
        CHECKPOINT_VERSION,
        latest_completed_checkpoint_iteration,
    )

    def fake_fetch(key: str):
        if key.endswith("after_iter_0003/resume.json"):
            return {"version": CHECKPOINT_VERSION}
        return None

    with patch("app.utils.s3_utils.try_fetch_json_from_s3", side_effect=fake_fetch):
        assert (
            latest_completed_checkpoint_iteration(
                base_tiered="rooms/x/tiered_data", job_id="job-a", scan_max=5
            )
            == 3
        )


def test_build_resume_payload_and_checkpoint_paths():
    from app.services.linkedin_sgo_pipeline_service_async.linkedin_sgo_pipeline_async_checkpoint import (
        build_resume_payload,
        checkpoint_iteration_prefix,
        checkpoint_root,
    )

    doc = build_resume_payload(
        iteration_completed=2,
        num_iterations_planned=5,
        tier_mode="tier1",
        qualifying_keys_for_next=[["k"]],
        pred_snapshot_jsonable={"user_a": {"x": 1}},
    )
    assert doc["version"] == 1
    assert doc["next_outer_iteration"] == 3
    root = checkpoint_root("rooms/x/tiered_data", "job-uuid")
    assert root.endswith("/linkedin_sgo/checkpoints/job-uuid")
    pre = checkpoint_iteration_prefix(root, 2)
    assert pre.endswith("/after_iter_0002")


def test_worker_finalize_chunk_promotes_checkpoint():
    """Stub repo + room lookup; real ``app.database`` stays loadable (``from app import database``)."""
    job = {
        "job_id": "j1",
        "audience_room_id": "r1",
        "job_type": JOB_TYPE_LINKEDIN_SGO_PIPELINE,
        "status": "PROCESSING",
        "model": None,
        "result": {
            "num_iterations": 1,
            "outer_iteration_next": 2,
            "tier_mode": "tier1",
            "tier_int": 1,
        },
    }
    room = SimpleNamespace(source="linkedin-audience")

    fake_cfg = types.ModuleType("config")
    fake_cfg.logger = logging.getLogger("test_sgo")
    fake_cfg.s3_bucket = "bucket"
    fake_cfg.s3_client = MagicMock()
    fake_cfg.s3_region = "us-west-2"

    fake_repo = types.ModuleType("linkedin_room_pipeline_job_repository")
    fake_repo.JOB_TYPE_LINKEDIN_SGO_PIPELINE = JOB_TYPE_LINKEDIN_SGO_PIPELINE
    fake_repo.get_linkedin_room_pipeline_job = MagicMock(return_value=job)
    fake_repo.update_linkedin_room_pipeline_job = MagicMock()

    saved: dict[str, object | None] = {}
    worker_key = "app.services.linkedin_sgo_pipeline_service_async.linkedin_sgo_pipeline_async_worker"
    try:
        saved["app.config"] = _install_sys_module("app.config", fake_cfg)
        saved[
            "app.services.linkedin_room_pipeline_async.linkedin_room_pipeline_job_repository"
        ] = _install_sys_module(
            "app.services.linkedin_room_pipeline_async.linkedin_room_pipeline_job_repository",
            fake_repo,
        )
        sys.modules.pop(worker_key, None)

        wmod = importlib.import_module(worker_key)

        async def _run():
            with patch.object(
                wmod,
                "promote_checkpoint_to_canonical_artifacts",
                return_value={"predictions": "https://example/pred"},
            ) as mp:
                with patch.object(wmod, "latest_completed_checkpoint_iteration", return_value=0):
                    with patch("app.database.find_audience_room_by_id", return_value=room):
                        await wmod.run_linkedin_sgo_pipeline_job_chunk(
                            job_id="j1",
                            audience_room_id="r1",
                            enterprise_name=None,
                            model=None,
                            base_url="http://localhost:8000",
                        )
            return fake_repo.update_linkedin_room_pipeline_job, mp

        mu, mp = asyncio.run(_run())
        mp.assert_called_once()
        final = [c for c in mu.call_args_list if c.kwargs.get("status") == "COMPLETED"]
        assert len(final) == 1
        assert final[0].kwargs["result"]["checkpoint_promoted"] is True
    finally:
        sys.modules.pop(worker_key, None)
        if "app.services.linkedin_room_pipeline_async.linkedin_room_pipeline_job_repository" in saved:
            _restore_sys_module(
                "app.services.linkedin_room_pipeline_async.linkedin_room_pipeline_job_repository",
                saved[
                    "app.services.linkedin_room_pipeline_async.linkedin_room_pipeline_job_repository"
                ],
            )
        if "app.config" in saved:
            _restore_sys_module("app.config", saved["app.config"])


def test_worker_missing_s3_marks_failed():
    fake_cfg = types.ModuleType("config")
    fake_cfg.logger = logging.getLogger("test_sgo")
    fake_cfg.s3_bucket = None
    fake_cfg.s3_client = None
    fake_cfg.s3_region = "us-west-2"

    fake_repo = types.ModuleType("linkedin_room_pipeline_job_repository")
    fake_repo.JOB_TYPE_LINKEDIN_SGO_PIPELINE = JOB_TYPE_LINKEDIN_SGO_PIPELINE
    fake_repo.get_linkedin_room_pipeline_job = MagicMock(return_value=None)
    fake_repo.update_linkedin_room_pipeline_job = MagicMock()

    saved: dict[str, object | None] = {}
    worker_key = "app.services.linkedin_sgo_pipeline_service_async.linkedin_sgo_pipeline_async_worker"
    try:
        saved["app.config"] = _install_sys_module("app.config", fake_cfg)
        saved[
            "app.services.linkedin_room_pipeline_async.linkedin_room_pipeline_job_repository"
        ] = _install_sys_module(
            "app.services.linkedin_room_pipeline_async.linkedin_room_pipeline_job_repository",
            fake_repo,
        )
        sys.modules.pop(worker_key, None)

        wmod = importlib.import_module(worker_key)

        async def _run():
            await wmod.run_linkedin_sgo_pipeline_job_chunk(
                job_id="j1",
                audience_room_id="r1",
                enterprise_name=None,
                model=None,
                base_url="http://localhost:8000",
            )

        asyncio.run(_run())
        mu = fake_repo.update_linkedin_room_pipeline_job
        mu.assert_called_once()
        assert mu.call_args.kwargs["status"] == "FAILED"
        assert "S3" in (mu.call_args.kwargs.get("error") or "")
    finally:
        sys.modules.pop(worker_key, None)
        if "app.services.linkedin_room_pipeline_async.linkedin_room_pipeline_job_repository" in saved:
            _restore_sys_module(
                "app.services.linkedin_room_pipeline_async.linkedin_room_pipeline_job_repository",
                saved[
                    "app.services.linkedin_room_pipeline_async.linkedin_room_pipeline_job_repository"
                ],
            )
        if "app.config" in saved:
            _restore_sys_module("app.config", saved["app.config"])


def test_sgo_job_progress_counts():
    """Import worker only after stubbed-worker tests (worker import loads ``app.database``)."""
    from app.services.linkedin_sgo_pipeline_service_async.linkedin_sgo_pipeline_async_worker import (
        _sgo_failed_result_meta,
        _sgo_job_progress_counts,
    )
    from app.services.linkedin_sgo_pipeline_service_async.linkedin_sgo_pipeline_async_errors import (
        LinkedInSGOPipelineError,
    )
    import psycopg2

    assert _sgo_job_progress_counts(5, 5, 0) == (25, 0)
    assert _sgo_job_progress_counts(5, 5, 3) == (25, 15)
    assert _sgo_job_progress_counts(5, 5, 5) == (25, 25)
    assert _sgo_job_progress_counts(0, 5, 3) == (5, 3)

    meta_p = _sgo_failed_result_meta({"tier_mode": "tier1"}, LinkedInSGOPipelineError("x"))
    assert meta_p["sgo_failure_class"] == "pipeline"
    assert meta_p["sgo_resume_blocked"] is True

    meta_db = _sgo_failed_result_meta({}, psycopg2.OperationalError("closed"))
    assert meta_db["sgo_failure_class"] == "transient_db"
    assert meta_db["sgo_resume_blocked"] is False


def test_linkedin_sgo_async_routes_registered():
    """Lightweight guard: full ``api_router`` import loads most of the app (DB, etc.)."""
    root = Path(__file__).resolve().parents[1]
    ep = root / "app/api/v1/endpoints/linkedin_room_pipeline_async.py"
    text = ep.read_text(encoding="utf-8")
    assert (
        '@router.post("/api/v1/audience-rooms/{audience_room_id}/linkedin-sgo-pipeline/async")'
        in text
    )
    assert (
        '@router.post("/api/v1/audience-rooms/{audience_room_id}/linkedin-sgo-pipeline/async/process")'
        in text
    )
    assert (
        '"/api/v1/audience-rooms/{audience_room_id}/linkedin-sgo-pipeline/async/status/{job_id}"'
        in text
    )
    assert "JOB_TYPE_LINKEDIN_SGO_PIPELINE" in text


def test_upload_json_worker_raises_linkedinsgo_error():
    from app.services.linkedin_sgo_pipeline_service_async.linkedin_sgo_pipeline_async_errors import (
        LinkedInSGOPipelineError,
    )
    import app.services.linkedin_sgo_pipeline_service_async.linkedin_sgo_pipeline_async_s3_workersafe as ws

    mock_client = MagicMock()
    mock_client.put_object.side_effect = RuntimeError("boom")
    with patch("app.config.s3_client", mock_client):
        with patch("app.config.s3_bucket", "bucket"):
            try:
                ws.upload_json_to_s3_worker("some/key.json", {"a": 1})
            except LinkedInSGOPipelineError as e:
                assert "upload failed" in str(e).lower() or "S3" in str(e)
            else:
                raise AssertionError("expected LinkedInSGOPipelineError")
