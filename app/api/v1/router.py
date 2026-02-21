"""Main API router that imports all endpoint routers."""
from fastapi import APIRouter

# Import endpoint routers
from .endpoints import scrape, preview, audience, parallel_search, apify_search, apify_enrich, classifier_async, scrape_parallel, summaries_async
from extra_endpoints import router as extra_endpoints_router

api_router = APIRouter()

# Register endpoint routers
api_router.include_router(extra_endpoints_router)  # remove-labels, enrich, extract-filters, search, parallel/preview
api_router.include_router(scrape.router)
api_router.include_router(scrape_parallel.router)  # Parallel/batched scraping for faster processing
api_router.include_router(preview.router)
api_router.include_router(audience.router)
api_router.include_router(classifier_async.router)  # Async classifier for long-running jobs
api_router.include_router(parallel_search.router)
api_router.include_router(apify_search.router)
api_router.include_router(apify_enrich.router)
api_router.include_router(summaries_async.router)  # Async profile summaries for large rooms