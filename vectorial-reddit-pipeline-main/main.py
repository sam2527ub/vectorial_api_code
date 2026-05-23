"""
Main entry point for Vercel deployment.
"""
from app.main import app
import uvicorn

__all__ = ["app"]

# Local development - run uvicorn server
if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=9000, reload=True)

