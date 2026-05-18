"""Base URL for self-triggering async chunk processing (summaries, LinkedIn pipeline, etc.)."""

import os


def get_base_url() -> str:
    """Public origin used for HTTP self-triggers (e.g. POST .../async/process).

    Precedence (Vercel-friendly):

    1. **AUDIENCE_API_BASE_URL** — Set this when you need an explicit public URL:
       custom domain, stable URL across preview vs production, or when the Next.js
       orchestrator must match the same hostname FastAPI uses for callbacks.

    2. **VERCEL_URL** — Auto-set on Vercel; defaults callbacks to this deployment's
       ``https://<deployment>.vercel.app`` host.

    3. Hardcoded fallback for legacy deployments.
    """
    explicit = (os.getenv("AUDIENCE_API_BASE_URL") or "").strip()
    if explicit:
        return explicit.rstrip("/")

    vercel_url = (os.getenv("VERCEL_URL") or "").strip()
    if vercel_url:
        return f"https://{vercel_url.rstrip('/')}"

    return "https://audience-workflow.vercel.app"
