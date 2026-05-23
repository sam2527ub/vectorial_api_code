"""Launch ECS Fargate tasks for LinkedIn SGO workers."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.runtime_settings import get_runtime_settings

logger = logging.getLogger(__name__)


class SgoFargateLaunchError(RuntimeError):
    """ECS RunTask failed or Fargate is not configured."""


def _ecs_client():
    region = (
        os.getenv("AWS_REGION")
        or os.getenv("AWS_DEFAULT_REGION")
        or get_runtime_settings().aws.region_default
    )
    return boto3.client("ecs", region_name=region)


def _fargate_settings():
    return get_runtime_settings().sgo_fargate


def fargate_is_configured() -> bool:
    cfg = _fargate_settings()
    if not cfg.enabled:
        return False
    if not (cfg.ecs_cluster or "").strip():
        return False
    if not (cfg.task_definition or "").strip():
        return False
    if not cfg.subnet_ids:
        return False
    return True


def build_webhook_url(base_url: str) -> str:
    path = (_fargate_settings().webhook_path or "/api/v1/sgo/fargate/webhook").strip()
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base_url.rstrip('/')}{path}"


def launch_sgo_fargate_task(
    *,
    job_id: str,
    audience_room_id: str,
    webhook_url: Optional[str] = None,
    enterprise_name: Optional[str] = None,
    model: Optional[str] = None,
    tier_mode: str = "both",
    num_iterations: int = 5,
    notify_webhook: bool = False,
    tier1_job_id: Optional[str] = None,
    tier2_job_id: Optional[str] = None,
    pipeline_run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Start a one-shot Fargate task. Environment variables are passed to the worker container.

    ``WEBHOOK_URL`` is omitted when ``notify_webhook`` is false (poll-only workflows).
    """
    cfg = _fargate_settings()
    if not fargate_is_configured():
        raise SgoFargateLaunchError(
            "SGO Fargate is not configured. Set sgo_fargate.enabled=true and ECS cluster, "
            "task definition, subnet_ids in config/runtime.yaml or SGO_FARGATE_* env vars."
        )

    env_vars: List[Dict[str, str]] = [
        {"name": "AUDIENCE_ROOM_ID", "value": audience_room_id},
        {"name": "NOTIFY_WEBHOOK", "value": "true" if notify_webhook else "false"},
    ]
    if notify_webhook and webhook_url:
        env_vars.append({"name": "WEBHOOK_URL", "value": webhook_url})
    tm = (tier_mode or "both").strip().lower()
    if tm not in ("both", "all", "tier1_tier2", "tier1+tier2"):
        env_vars.append({"name": "JOB_ID", "value": job_id})
    if enterprise_name:
        env_vars.append({"name": "ENTERPRISE_NAME", "value": enterprise_name})
    if model:
        env_vars.append({"name": "MODEL", "value": model})

    env_vars.append({"name": "TIER_MODE", "value": (tier_mode or "both").strip()})
    env_vars.append({"name": "NUM_ITERATIONS", "value": str(max(1, int(num_iterations)))})
    if tier1_job_id:
        env_vars.append({"name": "TIER1_JOB_ID", "value": tier1_job_id})
    if tier2_job_id:
        env_vars.append({"name": "TIER2_JOB_ID", "value": tier2_job_id})
    if pipeline_run_id:
        env_vars.append({"name": "PIPELINE_RUN_ID", "value": pipeline_run_id})

    awsvpc: Dict[str, Any] = {
        "subnets": list(cfg.subnet_ids),
        "assignPublicIp": "ENABLED" if cfg.assign_public_ip else "DISABLED",
    }
    if cfg.security_group_ids:
        awsvpc["securityGroups"] = list(cfg.security_group_ids)

    container_override: Dict[str, Any] = {
        "name": cfg.container_name,
        "environment": env_vars,
    }

    run_task_kwargs: Dict[str, Any] = {
        "cluster": cfg.ecs_cluster.strip(),
        "taskDefinition": cfg.task_definition.strip(),
        "launchType": cfg.launch_type or "FARGATE",
        "networkConfiguration": {"awsvpcConfiguration": awsvpc},
        "overrides": {"containerOverrides": [container_override]},
        "startedBy": f"sgo-job-{job_id[:8]}",
    }
    if (cfg.task_role_arn or "").strip():
        run_task_kwargs["overrides"]["taskRoleArn"] = cfg.task_role_arn.strip()
    if (cfg.execution_role_arn or "").strip():
        run_task_kwargs["overrides"]["executionRoleArn"] = cfg.execution_role_arn.strip()

    try:
        response = _ecs_client().run_task(**run_task_kwargs)
    except (ClientError, BotoCoreError) as e:
        logger.error("[SGO_FARGATE] RunTask failed for job %s: %s", job_id, e, exc_info=True)
        raise SgoFargateLaunchError(str(e)) from e

    failures = response.get("failures") or []
    if failures:
        msg = failures[0].get("reason") or str(failures)
        logger.error("[SGO_FARGATE] RunTask failures for job %s: %s", job_id, failures)
        raise SgoFargateLaunchError(msg)

    tasks = response.get("tasks") or []
    task_arn = tasks[0].get("taskArn") if tasks else None
    logger.info(
        "[SGO_FARGATE] Started task for job %s audience_room=%s task_arn=%s",
        job_id,
        audience_room_id,
        task_arn,
    )
    return response
