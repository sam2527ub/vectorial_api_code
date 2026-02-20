"""Factory for post classifier LLM client."""
from app.config import logger

from .groq_classifier_client import GroqPostClassifierClient


def get_classifier_client():
    """Return configured classifier client (Groq) or None."""
    try:
        client = GroqPostClassifierClient()
        if client.is_configured():
            logger.debug("Using Groq client for post classification")
            return client
        logger.warning("Groq client not configured (set GROQ_API_KEY)")
        return None
    except Exception as e:
        logger.error(f"Failed to create classifier client: {e}")
        return None
