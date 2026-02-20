"""Base URL for self-triggering summarization chunk processing."""
import os


def get_base_url() -> str:
    """Get the base URL for self-triggering API calls."""
    vercel_url = os.getenv("VERCEL_URL")
    if vercel_url:
        return f"https://{vercel_url}"
    api_url = os.getenv("AUDIENCE_API_BASE_URL")
    if api_url:
        return api_url
    return "https://audience-workflow.vercel.app"
