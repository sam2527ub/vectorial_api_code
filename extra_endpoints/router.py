"""Aggregate router for all extra endpoints (moved out of main API)."""
from fastapi import APIRouter

from extra_endpoints.enrich import router as enrich_router
from extra_endpoints.search import router as search_router
from extra_endpoints.parallel_preview import router as parallel_preview_router

router = APIRouter()
router.include_router(enrich_router)
router.include_router(search_router)
router.include_router(parallel_preview_router)
