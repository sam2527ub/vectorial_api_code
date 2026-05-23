"""Factory for AI clients."""
from .interface import ContextSummaryClientInterface
from .groq_client import GroqContextSummaryClient
from app.services.comment_context_summary_service.config import CommentContextSummaryConfig


def get_ai_client(config: CommentContextSummaryConfig = None) -> ContextSummaryClientInterface:
    """Get AI client instance. Returns GroqContextSummaryClient by default."""
    return GroqContextSummaryClient(config)
