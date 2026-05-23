"""Main API router that imports all endpoint routers."""
from fastapi import APIRouter

# Import endpoint routers
from .endpoints import (
    subreddit_user_extraction,
    user_observation_fetch,
    subreddit_similarity_filter,
    audience_rooms,
    reddit_workflow_jobs,
    web_indexing,
    post_dimension_tagging,
    comment_dimension_tagging,
    comment_context_summary
)

api_router = APIRouter()

# Register endpoint routers
api_router.include_router(subreddit_user_extraction.router)
api_router.include_router(user_observation_fetch.router)
api_router.include_router(subreddit_similarity_filter.router)
api_router.include_router(audience_rooms.router)
api_router.include_router(reddit_workflow_jobs.router)
api_router.include_router(web_indexing.router)
api_router.include_router(post_dimension_tagging.router)
api_router.include_router(comment_dimension_tagging.router)
api_router.include_router(comment_context_summary.router)

