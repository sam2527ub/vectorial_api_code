"""
Main entry point - imports app from modular structure.

- config/runtime.yaml — server host/port/reload, pool sizes, scraper IDs, pipeline YAML paths, etc.
- app/config.py — client initialization (secrets still from ``.env``)
"""
from dotenv import load_dotenv

from app.main import app
import uvicorn

# Export app for Vercel serverless function deployment
__all__ = ["app"]

if __name__ == "__main__":
    load_dotenv()
    from app.runtime_settings import get_runtime_settings

    s = get_runtime_settings().server
    uvicorn.run("app.main:app", host=s.host, port=s.port, reload=s.reload)