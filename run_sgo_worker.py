#!/usr/bin/env python3
"""
Fargate entrypoint for LinkedIn SGO training.

Reads AUDIENCE_ROOM_ID (required) and optional JOB_ID / tier job ids from the environment,
runs all SGO outer-iteration chunks, and optionally POSTs completion to the API webhook.

Poll-only: omit WEBHOOK_URL or set NOTIFY_WEBHOOK=false — job status is always in Postgres.
"""

from __future__ import annotations

import asyncio
import os
import sys
import traceback

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


async def _run() -> dict:
    from app.runtime_settings import get_runtime_settings
    from app.services.sgo_fargate.sgo_fargate_worker_loop import run_sgo_training_loop
    from app.services.sgo_fargate.sgo_tier_pipeline import run_sgo_tier_pipeline
    from app.services.sgo_fargate.webhook_delivery import post_sgo_webhook_with_retries

    job_id = _env("JOB_ID")
    webhook_url = _env("WEBHOOK_URL")
    notify_webhook = _env_bool("NOTIFY_WEBHOOK", default=bool(webhook_url))
    force_resume = _env_bool("SGO_FORCE_RESUME", default=False)
    audience_room_id = _env("AUDIENCE_ROOM_ID")
    enterprise_name = _env("ENTERPRISE_NAME") or None
    model = _env("MODEL") or None
    tier_mode = _env("TIER_MODE") or "both"
    num_iterations = int(_env("NUM_ITERATIONS") or "5")
    tier1_job_id = _env("TIER1_JOB_ID")
    tier2_job_id = _env("TIER2_JOB_ID")
    pipeline_run_id = _env("PIPELINE_RUN_ID")

    if not audience_room_id:
        print("Missing AUDIENCE_ROOM_ID. Exiting.")
        return {"status": "FAILED", "error": "missing_audience_room_id"}

    tm = tier_mode.strip().lower()
    use_tier_pipeline = tm in ("both", "all", "tier1_tier2", "tier1+tier2")
    if not use_tier_pipeline and not job_id:
        print("Single-tier mode requires JOB_ID. Exiting.")
        return {"status": "FAILED", "error": "missing_job_id"}

    tier_job_ids = {}
    if tier1_job_id:
        tier_job_ids["tier1"] = tier1_job_id
    if tier2_job_id:
        tier_job_ids["tier2"] = tier2_job_id

    if use_tier_pipeline:
        print(
            f"Starting SGO tier pipeline mode={tier_mode!r} room={audience_room_id} "
            f"num_iterations={num_iterations} notify_webhook={notify_webhook} force_resume={force_resume}"
        )
    else:
        print(
            f"Starting SGO single job={job_id} room={audience_room_id} "
            f"tier_mode={tier_mode} notify_webhook={notify_webhook} force_resume={force_resume}"
        )

    try:
        if use_tier_pipeline:
            outcome = await run_sgo_tier_pipeline(
                audience_room_id=audience_room_id,
                enterprise_name=enterprise_name,
                model=model,
                num_iterations=num_iterations,
                tier_mode=tier_mode,
                tier_job_ids=tier_job_ids or None,
                force_resume=force_resume,
            )
        else:
            outcome = await run_sgo_training_loop(
                job_id=job_id,
                audience_room_id=audience_room_id,
                enterprise_name=enterprise_name,
                model=model,
                force_resume=force_resume,
            )
        status = str(outcome.get("status") or "FAILED").upper()
        tier_results = outcome.get("tier_results")
        payload: dict = {
            "audience_room_id": audience_room_id,
            "pipeline_mode": "tier1_then_tier2" if use_tier_pipeline else "single_tier",
        }
        if pipeline_run_id:
            payload["pipeline_run_id"] = pipeline_run_id
        if status == "COMPLETED":
            payload.update(
                {
                    "job_id": job_id or None,
                    "status": "COMPLETED",
                    "message": outcome.get("message") or "SGO training finished successfully.",
                    "tier_results": tier_results,
                }
            )
            if use_tier_pipeline and isinstance(tier_results, list) and tier_results:
                payload["job_id"] = str(tier_results[-1].get("job_id") or "") or None
        else:
            payload.update(
                {
                    "job_id": job_id or None,
                    "status": "FAILED",
                    "error": outcome.get("error") or "SGO training did not complete",
                    "tier_results": tier_results,
                    "failed_tier": outcome.get("failed_tier"),
                }
            )
            if use_tier_pipeline and isinstance(tier_results, list):
                for tr in tier_results:
                    if str(tr.get("status") or "").upper() == "FAILED":
                        payload["job_id"] = str(tr.get("job_id") or "") or payload.get("job_id")
                        break
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"Training failed:\n{error_details}")
        payload = {
            "job_id": job_id or None,
            "status": "FAILED",
            "error": str(e),
            "audience_room_id": audience_room_id,
            "pipeline_mode": "tier1_then_tier2" if use_tier_pipeline else "single_tier",
        }
        if pipeline_run_id:
            payload["pipeline_run_id"] = pipeline_run_id

    if notify_webhook and webhook_url:
        print(f"Calling webhook at {webhook_url} ...")
        secret = _env("SGO_FARGATE_WEBHOOK_SECRET")
        sgo_cfg = get_runtime_settings().linkedin_sgo_async
        delivery = await post_sgo_webhook_with_retries(
            webhook_url=webhook_url,
            payload=payload,
            secret=secret or None,
            max_attempts=int(sgo_cfg.webhook_max_attempts),
            initial_backoff_s=float(sgo_cfg.webhook_initial_backoff_s),
        )
        if not delivery.get("ok"):
            print(f"Webhook delivery failed after retries: {delivery}")
            payload["webhook_delivery_error"] = delivery.get("error")
        else:
            print("Successfully notified API.")
    else:
        print(
            "Poll-only mode: skipped webhook (NOTIFY_WEBHOOK=false or WEBHOOK_URL unset). "
            "Job status is in Postgres — poll tier_results[].poll or poll_pipeline."
        )

    return payload


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
