"""LLM client interface for post classification."""
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class PostClassifierClientInterface(ABC):
    """Interface for LLM clients that classify posts (e.g. useful / not useful)."""

    @abstractmethod
    async def classify_posts_single_call(
        self,
        posts_texts: List[str],
        classifier_name: str,
        classifier_prompt: str,
        classifier_description: str,
        classifier_labels: List[str],
        classifier_examples: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:
        """
        Classify multiple posts in a single API call.
        Returns list of dicts with keys: label, score, allScores.
        """
        pass

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True if client is configured and ready to use."""
        pass
