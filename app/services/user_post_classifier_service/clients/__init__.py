"""Clients for User Post Classifier service (classification uses ai_gateway in batch_runner)."""
from .llm import PostClassifierClientInterface

__all__ = ["PostClassifierClientInterface"]
