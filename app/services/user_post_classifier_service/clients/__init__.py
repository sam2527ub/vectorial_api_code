"""Clients for User Post Classifier service."""
from .llm import get_classifier_client, PostClassifierClientInterface

__all__ = ["get_classifier_client", "PostClassifierClientInterface"]
