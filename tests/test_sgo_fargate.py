"""Tests for SGO Fargate ECS launcher and webhook (no real AWS calls)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.runtime_settings import reset_runtime_settings_cache


@pytest.fixture(autouse=True)
def _reset_settings():
    reset_runtime_settings_cache()
    yield
    reset_runtime_settings_cache()


def test_fargate_is_configured_requires_subnets(monkeypatch):
    monkeypatch.setenv("RUNTIME_CONFIG_PATH", "")
    from app.services.sgo_fargate.ecs_client import fargate_is_configured

    with patch("app.services.sgo_fargate.ecs_client.get_runtime_settings") as m:
        cfg = MagicMock()
        cfg.enabled = True
        cfg.ecs_cluster = "cluster"
        cfg.task_definition = "task:1"
        cfg.subnet_ids = []
        m.return_value.sgo_fargate = cfg
        assert fargate_is_configured() is False

        cfg.subnet_ids = ["subnet-a"]
        assert fargate_is_configured() is True


def test_build_webhook_url():
    from app.services.sgo_fargate.ecs_client import build_webhook_url

    with patch("app.services.sgo_fargate.ecs_client.get_runtime_settings") as m:
        cfg = MagicMock()
        cfg.webhook_path = "/api/v1/sgo/fargate/webhook"
        m.return_value.sgo_fargate = cfg
        assert (
            build_webhook_url("https://api.example.com")
            == "https://api.example.com/api/v1/sgo/fargate/webhook"
        )


def test_launch_sgo_fargate_task_passes_env():
    from app.services.sgo_fargate.ecs_client import launch_sgo_fargate_task

    mock_ecs = MagicMock()
    mock_ecs.run_task.return_value = {"tasks": [{"taskArn": "arn:task:1"}], "failures": []}

    with patch("app.services.sgo_fargate.ecs_client.fargate_is_configured", return_value=True):
        with patch("app.services.sgo_fargate.ecs_client._ecs_client", return_value=mock_ecs):
            with patch("app.services.sgo_fargate.ecs_client.get_runtime_settings") as m:
                cfg = MagicMock()
                cfg.ecs_cluster = "c1"
                cfg.task_definition = "sgo-worker:3"
                cfg.container_name = "sgo-python-container"
                cfg.launch_type = "FARGATE"
                cfg.assign_public_ip = True
                cfg.subnet_ids = ["subnet-1"]
                cfg.security_group_ids = ["sg-1"]
                cfg.task_role_arn = ""
                cfg.execution_role_arn = ""
                m.return_value.sgo_fargate = cfg

                launch_sgo_fargate_task(
                    job_id="job-1",
                    audience_room_id="room-1",
                    webhook_url="https://api.example.com/hook",
                    enterprise_name="beta",
                    model="gpt-4.1",
                    tier_mode="tier1",
                    num_iterations=5,
                    notify_webhook=True,
                )

    call = mock_ecs.run_task.call_args.kwargs
    env = call["overrides"]["containerOverrides"][0]["environment"]
    names = {e["name"]: e["value"] for e in env}
    assert names["JOB_ID"] == "job-1"
    assert names["TIER_MODE"] == "tier1"
    assert names["NUM_ITERATIONS"] == "5"
    assert names["AUDIENCE_ROOM_ID"] == "room-1"
    assert names["WEBHOOK_URL"] == "https://api.example.com/hook"
    assert names["ENTERPRISE_NAME"] == "beta"
    assert names["MODEL"] == "gpt-4.1"
    assert names["NOTIFY_WEBHOOK"] == "true"


def test_launch_sgo_fargate_task_poll_only_omits_webhook_url():
    from app.services.sgo_fargate.ecs_client import launch_sgo_fargate_task

    mock_ecs = MagicMock()
    mock_ecs.run_task.return_value = {"tasks": [{"taskArn": "arn:task:1"}], "failures": []}

    with patch("app.services.sgo_fargate.ecs_client.fargate_is_configured", return_value=True):
        with patch("app.services.sgo_fargate.ecs_client._ecs_client", return_value=mock_ecs):
            with patch("app.services.sgo_fargate.ecs_client.get_runtime_settings") as m:
                cfg = MagicMock()
                cfg.ecs_cluster = "c1"
                cfg.task_definition = "sgo-worker:3"
                cfg.container_name = "sgo-python-container"
                cfg.launch_type = "FARGATE"
                cfg.assign_public_ip = True
                cfg.subnet_ids = ["subnet-1"]
                cfg.security_group_ids = []
                cfg.task_role_arn = ""
                cfg.execution_role_arn = ""
                m.return_value.sgo_fargate = cfg

                launch_sgo_fargate_task(
                    job_id="job-1",
                    audience_room_id="room-1",
                    tier_mode="both",
                    tier1_job_id="t1",
                    tier2_job_id="t2",
                    pipeline_run_id="pipe-1",
                    notify_webhook=False,
                )

    env = mock_ecs.run_task.call_args.kwargs["overrides"]["containerOverrides"][0]["environment"]
    names = {e["name"]: e["value"] for e in env}
    assert "WEBHOOK_URL" not in names
    assert names["NOTIFY_WEBHOOK"] == "false"
    assert names["TIER1_JOB_ID"] == "t1"
    assert names["TIER2_JOB_ID"] == "t2"


def test_run_sgo_training_loop_stops_on_completed():
    from app.services.sgo_fargate.sgo_fargate_worker_loop import run_sgo_training_loop

    statuses = ["PROCESSING", "COMPLETED"]

    def fake_get_job(job_id, enterprise_name=None):
        return {
            "job_id": job_id,
            "job_type": "linkedin_sgo_pipeline",
            "audience_room_id": "room-1",
            "status": statuses.pop(0) if statuses else "COMPLETED",
        }

    with patch(
        "app.services.sgo_fargate.sgo_fargate_worker_loop.get_linkedin_room_pipeline_job",
        side_effect=fake_get_job,
    ):
        with patch(
            "app.services.sgo_fargate.sgo_fargate_worker_loop.run_linkedin_sgo_pipeline_job_chunk",
            new_callable=AsyncMock,
        ) as chunk:
            out = asyncio.run(
                run_sgo_training_loop(
                    job_id="job-1",
                    audience_room_id="room-1",
                )
            )
            assert out["status"] == "COMPLETED"
            chunk.assert_awaited_once()


def test_aggregate_fargate_pipeline_status_completed():
    from app.services.sgo_fargate.sgo_fargate_pipeline_status import aggregate_fargate_pipeline_status

    def fake_job(job_id, enterprise_name=None):
        return {
            "job_id": job_id,
            "job_type": "linkedin_sgo_pipeline",
            "audience_room_id": "room-1",
            "status": "COMPLETED",
            "result": {"num_iterations": 5, "outer_iteration_next": 6},
        }

    with patch(
        "app.services.sgo_fargate.sgo_fargate_pipeline_status.get_linkedin_room_pipeline_job",
        side_effect=fake_job,
    ):
        out = aggregate_fargate_pipeline_status(
            audience_room_id="room-1",
            tier1_job_id="j1",
            tier2_job_id="j2",
            pipeline_run_id="pipe-1",
        )
    assert out["pipeline_status"] == "COMPLETED"
    assert len(out["tier_results"]) == 2


def test_webhook_handler_completed():
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
                        "job_id": "job-1",
                        "status": "COMPLETED",
                        "pipeline_mode": "single_tier",
                    },
                )
            )
            assert "complete" in resp["message"].lower()
            assert resp["synced_job_ids"] == ["job-1"]


def test_aggregate_fargate_pipeline_status_completed():
    from app.services.sgo_fargate.sgo_fargate_pipeline_status import aggregate_fargate_pipeline_status

    def fake_job(job_id, enterprise_name=None):
        return {
            "job_id": job_id,
            "job_type": "linkedin_sgo_pipeline",
            "audience_room_id": "room-1",
            "status": "COMPLETED",
            "result": {"num_iterations": 5, "outer_iteration_next": 6},
        }

    with patch(
        "app.services.sgo_fargate.sgo_fargate_pipeline_status.get_linkedin_room_pipeline_job",
        side_effect=fake_job,
    ):
        out = aggregate_fargate_pipeline_status(
            audience_room_id="room-1",
            tier1_job_id="j1",
            tier2_job_id="j2",
            pipeline_run_id="pipe-1",
        )
    assert out["pipeline_status"] == "COMPLETED"
    assert len(out["tier_results"]) == 2
