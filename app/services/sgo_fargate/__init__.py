"""AWS ECS Fargate integration for long-running LinkedIn SGO jobs."""

from .ecs_client import (
    SgoFargateLaunchError,
    fargate_is_configured,
    launch_sgo_fargate_task,
)
from .sgo_fargate_pipeline_status import aggregate_fargate_pipeline_status
from .sgo_fargate_worker_loop import run_sgo_training_loop

__all__ = [
    "SgoFargateLaunchError",
    "SgoFargateResumeError",
    "aggregate_fargate_pipeline_status",
    "assess_fargate_pipeline_resume",
    "fargate_is_configured",
    "launch_sgo_fargate_task",
    "resume_linkedin_sgo_on_fargate",
    "start_linkedin_sgo_on_fargate",
    "run_sgo_training_loop",
    "handle_sgo_fargate_webhook",
]


def __getattr__(name: str):
    """Lazy imports so Fargate worker does not load API-only modules (e.g. prompts)."""
    if name == "start_linkedin_sgo_on_fargate":
        from .fargate_launcher import start_linkedin_sgo_on_fargate

        return start_linkedin_sgo_on_fargate
    if name == "handle_sgo_fargate_webhook":
        from .webhook_handler import handle_sgo_fargate_webhook

        return handle_sgo_fargate_webhook
    if name == "resume_linkedin_sgo_on_fargate":
        from .fargate_resume import resume_linkedin_sgo_on_fargate

        return resume_linkedin_sgo_on_fargate
    if name == "assess_fargate_pipeline_resume":
        from .fargate_resume import assess_fargate_pipeline_resume

        return assess_fargate_pipeline_resume
    if name == "SgoFargateResumeError":
        from .fargate_resume import SgoFargateResumeError

        return SgoFargateResumeError
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
