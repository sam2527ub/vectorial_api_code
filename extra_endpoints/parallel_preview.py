"""Extra endpoint: Parallel search preview (fixed model and match_limit)."""
import os
from fastapi import APIRouter, HTTPException

from app.models.schemas import ParallelSearchRequest, ParallelSearchPreviewRequest

router = APIRouter()


@router.post("/api/search/parallel/preview")
async def search_parallel_preview_stream(payload: ParallelSearchPreviewRequest):
    """
    Preview search for LinkedIn profiles using Parallel AI FindAll API with real-time SSE streaming.
    Identical to /api/search/parallel but with fixed parameters: model="core", match_limit=5.
    """
    parallel_api_key = os.getenv("PARALLEL_API_KEY")
    if not parallel_api_key:
        raise HTTPException(
            status_code=503,
            detail="PARALLEL_API_KEY not configured. Please set the environment variable."
        )

    fixed_payload = ParallelSearchRequest(
        query=payload.query,
        model="core",
        match_limit=5
    )
    from app.api.v1.endpoints.parallel_search import search_parallel_stream
    return await search_parallel_stream(fixed_payload)
