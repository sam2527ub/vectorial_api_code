"""
FastAPI application entry point with modular structure.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from app import database
from app.config import logger
from app.api.v1.router import api_router
from app.utils.s3_utils import initialize_all_enterprise_audience_folders


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan event handler for startup and shutdown."""
    # Startup - verify database connections
    if database.is_main_db_available():
        if database.check_main_db_connection():
            logger.info("Main database connection verified successfully")
        else:
            logger.warning("Main database connection check failed")
    else:
        logger.warning("Main database not configured - scraping endpoints will not work")
    
    if database.is_audience_db_available():
        if database.check_audience_db_connection():
            logger.info("Audience database connection verified successfully")
        else:
            logger.warning("Audience database connection check failed")
    else:
        logger.warning("Audience database not configured - audience endpoints will be disabled")
    
    # Initialize enterprise and audience type folders in S3
    try:
        logger.info("Initializing enterprise and audience folders in S3")
        initialize_all_enterprise_audience_folders()
    except Exception as e:
        logger.warning(f"Failed to initialize enterprise and audience folders on startup: {e}")
        # Don't fail startup if this fails - folders will be created on-demand
    
    yield
    
    # Shutdown - close connection pools
    database.close_pools()
    logger.info("Database connection pools closed")


# Create FastAPI app with lifespan
app = FastAPI(
    title="Profile Engine API",
    description="Backend for PDL Enrichment, Search, and LinkedIn Scraping",
    version="1.0.0",
    lifespan=lifespan
)

# Enable CORS (Allows your React frontend to talk to this Python backend)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API router
app.include_router(api_router)

# Health check endpoint
@app.get("/")
def health_check():
    """Simple health check endpoint."""
    return {"status": "ok", "message": "Backend is running"}


__all__ = ["app"]

