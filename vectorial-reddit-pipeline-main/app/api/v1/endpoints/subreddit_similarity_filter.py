"""Subreddit similarity filter endpoints.

Uses database (SubredditSimilarityFilter table) for job tracking.
Uses chunked processing with self-trigger via HTTP so each chunk
runs in a new Vercel invocation and progress is persisted."""
import os
from typing import Optional
from fastapi import APIRouter, HTTPException, Query, BackgroundTasks, Request, Body
from app.config import logger
from app.services.subreddit_similarity_filter_service import (
    request_handler,
    SubredditFilterRequest,
)
from app.services.subreddit_similarity_filter_service.schemas import (
    CreateSubredditFilterJobBody,
    ProcessSubredditFilterChunkBody,
)
from app.services.subreddit_similarity_filter_service.config import CHUNK_SIZE_SUBREDDITS
from app.services.subreddit_similarity_filter_service.repositories.subreddit_similarity_filter_job_repository import (
    create_subreddit_similarity_filter_job,
    find_subreddit_similarity_filter_job_by_id,
)

router = APIRouter()


def _get_base_url(request: Optional[Request] = None) -> str:
    """Base URL for self-triggering process chunks."""
    if request is not None:
        try:
            return str(request.base_url).rstrip("/")
        except Exception:
            pass
    vercel_url = os.getenv("VERCEL_URL")
    if vercel_url:
        return f"https://{vercel_url}"
    return os.getenv("SUBREDDIT_FILTER_BASE_URL", "https://vectorial-reddit-pipeline.vercel.app")


async def _trigger_process_endpoint(
    base_url: str,
    job_id: str,
    enterprise_name: Optional[str],
    chunk_size: int,
) -> None:
    """Call the process endpoint so the first chunk runs in a new invocation."""
    import httpx
    url = f"{base_url.rstrip('/')}/api/v1/filter/subreddit-similarity/process"
    json_body = {
        "jobId": job_id,
        "startSubredditIndex": 0,
        "chunkSize": chunk_size,
    }
    if enterprise_name is not None:
        json_body["enterpriseName"] = enterprise_name
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=json_body)
            response.raise_for_status()
        logger.info(f"Triggered process endpoint for subreddit filter job {job_id}")
    except Exception as e:
        if "ReadTimeout" in type(e).__name__:
            logger.info(
                f"Process endpoint triggered for job {job_id} (read timeout; chunk may still be running)"
            )
        else:
            logger.error(
                f"Failed to trigger process for job {job_id}: type={type(e).__name__} msg={e!r}",
                exc_info=True,
            )


# ── POST: Create job ─────────────────────────────────────────────────────────

@router.post("/api/v1/filter/subreddit-similarity")
async def create_subreddit_filter_job(
    request: Request,
    background_tasks: BackgroundTasks,
    body: CreateSubredditFilterJobBody = Body(..., embed=False),
) -> dict:
    """Create a subreddit similarity filter job and return job_id immediately.

    Processing runs in chunks via the /process endpoint.
    """
    logger.info("=" * 80)
    logger.info("REQUEST RECEIVED: POST /api/v1/filter/subreddit-similarity (async job)")
    logger.info(f"  - Matched Subreddits Count: {len(body.matched_subreddits)}")
    logger.info(f"  - Users Count: {len(body.users)}")
    logger.info(f"  - Activities Count: {len(body.activities) if body.activities else 0}")
    logger.info(f"  - Activities S3 URL: {body.activities_s3_url}")
    logger.info(f"  - Min Activities: {body.min_activities}")
    logger.info(f"  - Chunk Size: {body.chunk_size}")
    logger.info("=" * 80)

    if not request_handler:
        raise HTTPException(
            status_code=503,
            detail="AI client not configured. Please set AI provider API key environment variable.",
        )

    try:
        input_s3_key = request_handler.store_job_input(
            job_id="pending",
            matched_subreddits=body.matched_subreddits,
            users=body.users,
            activities=body.activities,
            activities_s3_url=body.activities_s3_url,
            min_activities=body.min_activities,
        )

        job = create_subreddit_similarity_filter_job(
            enterprise_name=body.enterprise_name,
            input_s3_key=input_s3_key,
        )

        real_input_s3_key = request_handler.store_job_input(
            job_id=job.id,
            matched_subreddits=body.matched_subreddits,
            users=body.users,
            activities=body.activities,
            activities_s3_url=body.activities_s3_url,
            min_activities=body.min_activities,
        )

        base_url = _get_base_url(request)
        background_tasks.add_task(
            _trigger_process_endpoint,
            base_url, job.id, body.enterprise_name, body.chunk_size,
        )

        logger.info(f"Subreddit similarity filter job created: {job.id}")

        return {
            "job_id": job.id,
            "status": "PENDING",
            "message": "Job created successfully. Use GET endpoint to check status.",
        }

    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Validation error: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating subreddit filter job: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create subreddit filter job: {str(e)}",
        )


# ── POST: Process chunk ──────────────────────────────────────────────────────

@router.post("/api/v1/filter/subreddit-similarity/process")
async def process_subreddit_filter_chunk(
    request: Request,
    body: ProcessSubredditFilterChunkBody = Body(..., embed=False),
) -> dict:
    """Process one chunk of subreddit similarity checks (internal, self-triggered)."""
    logger.debug(
        f"Process chunk: job={body.job_id}, startSubredditIndex={body.start_subreddit_index}"
    )

    if not request_handler:
        raise HTTPException(status_code=503, detail="AI client not configured.")

    base_url = _get_base_url(request)
    await request_handler.process_job_chunk(
        job_id=body.job_id,
        start_subreddit_index=body.start_subreddit_index,
        chunk_size=body.chunk_size,
        base_url=base_url,
        enterprise_name=body.enterprise_name,
    )
    return {
        "job_id": body.job_id,
        "status": "PROCESSING",
        "message": f"Chunk starting at subreddit {body.start_subreddit_index} processed",
    }


# ── GET: Job status ──────────────────────────────────────────────────────────

@router.get("/api/v1/filter/subreddit-similarity/{job_id}")
async def get_subreddit_filter_job_status(
    job_id: str,
    enterpriseName: Optional[str] = Query(None, description="Enterprise name for database routing"),
) -> dict:
    """Get the status of a subreddit similarity filter job.

    When COMPLETED, fetches the full result (including users/activities) from S3
    so the caller gets the same shape as the old sync endpoint.
    """
    logger.debug(f"GET subreddit-similarity status {job_id}")

    try:
        job = find_subreddit_similarity_filter_job_by_id(
            job_id=job_id, enterprise_name=enterpriseName,
        )

        if not job:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

        response: dict = {
            "job_id": job.id,
            "status": job.status,
            "total_subreddits": job.total_subreddits,
            "checked_subreddits": job.checked_subreddits,
            "total_users_before": job.total_users_before,
            "total_users_after": job.total_users_after,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "updated_at": job.updated_at.isoformat() if job.updated_at else None,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        }

        if job.error:
            response["error"] = job.error

        if job.status == "COMPLETED" and job.result_s3_key and request_handler:
            try:
                full_result = request_handler._download_json(job.result_s3_key)
                response["result"] = full_result
            except Exception as s3_err:
                logger.warning(f"Failed to fetch full result from S3 for job {job_id}: {s3_err}")
                if job.result:
                    response["result"] = job.result
        elif job.result:
            response["result"] = job.result

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching job status: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch job status: {str(e)}",
        )
