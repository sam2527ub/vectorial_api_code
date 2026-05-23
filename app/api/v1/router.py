"""Main API router that imports all endpoint routers."""
from fastapi import APIRouter

# Import endpoint routers
from .endpoints import (
    preview,
    audience,
    parallel_search,
    apify_search,
    apify_enrich,
    classifier_async,
    scrape_parallel,
    summaries_async,
    comments_scrape,
    comment_context_summary_async,
    linkedin_room_pipeline_async,
    linkedin_room_pipeline,
    sgo_fargate,
)
from extra_endpoints import router as extra_endpoints_router

api_router = APIRouter()

# Register endpoint routers
api_router.include_router(extra_endpoints_router)  # enrich, search, parallel/preview
api_router.include_router(scrape_parallel.router)  # Scraping (parallel only)
api_router.include_router(preview.router)
api_router.include_router(audience.router)
api_router.include_router(classifier_async.router)  # Async classifier for long-running jobs
api_router.include_router(parallel_search.router)
api_router.include_router(apify_search.router)
api_router.include_router(apify_enrich.router)
api_router.include_router(summaries_async.router)  # Async profile summaries for large rooms
api_router.include_router(
    linkedin_room_pipeline_async.router
)  # LinkedIn theme, stimulus, ground-truth async
api_router.include_router(
    linkedin_room_pipeline.router
)  # LinkedIn pipeline step status + run plan (skip completed steps)
api_router.include_router(comments_scrape.router)  # LinkedIn comments scrape (poll Apify, update commentsS3Url only)
api_router.include_router(comment_context_summary_async.router)  # Async comment context summaries on LinkedIn comments.json
api_router.include_router(sgo_fargate.router)  # LinkedIn SGO on AWS Fargate (external worker + webhook)