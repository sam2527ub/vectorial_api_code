"""Main API router that imports all endpoint routers."""
from fastapi import APIRouter

# Import endpoint routers
from .endpoints import enrich, search, scrape, preview, audience, classifier, parallel_search, apify_enrich

api_router = APIRouter()

# Register endpoint routers
api_router.include_router(enrich.router)
api_router.include_router(search.router)
api_router.include_router(scrape.router)
api_router.include_router(preview.router)
api_router.include_router(audience.router)
api_router.include_router(classifier.router)
api_router.include_router(parallel_search.router)
api_router.include_router(apify_enrich.router)