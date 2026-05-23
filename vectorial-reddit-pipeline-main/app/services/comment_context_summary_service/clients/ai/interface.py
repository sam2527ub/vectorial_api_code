"""Interface for AI clients."""
from abc import ABC, abstractmethod
from typing import Dict, Optional, Any


class ContextSummaryClientInterface(ABC):
    """Interface for AI clients that summarize comment context."""

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True if the client is properly configured."""
        pass

    @abstractmethod
    def get_raw_client(self) -> Any:
        """Return the underlying SDK client."""
        pass

    @abstractmethod
    async def summarize_comment_context(
        self,
        context: Dict[str, Optional[str]]
    ) -> str:
        """Summarize comment context using AI. Returns summary string."""
        pass
