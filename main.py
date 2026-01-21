"""
Main entry point - imports app from modular structure.

- app/config.py - Configuration and client initialization
- app/models/ - Pydantic request/response models  
- app/utils/ - Utility functions (helpers, S3, etc.)
- app/services/ - Business logic services
- app/api/v1/endpoints/ - All API endpoints organized by domain
"""
from app.main import app
import uvicorn

# Export app for Vercel serverless function deployment
__all__ = ["app"]

# Local development - run uvicorn server
if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
    //