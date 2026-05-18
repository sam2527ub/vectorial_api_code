"""Gateway handler interface (vectorial-reddit-pipeline style)."""
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Union


class GatewayHandlerInterface(ABC):
    """Interface for gateway handlers (Vercel, Portkey, etc.)."""

    @abstractmethod
    async def call(
        self,
        profile_id: str,
        messages: List[Dict[str, str]],
        max_tokens: int,
        model: str,
        fallback_models: Optional[List[str]] = None,
        provider_order: Optional[List[str]] = None,
        validate_summary: bool = False,
        return_text: bool = False,
        logprobs: Optional[bool] = False,
        top_logprobs: Optional[int] = None,
        return_full_response: bool = False,
        response_format: Optional[Dict[str, str]] = None,
        temperature: Optional[float] = None,
    ) -> Union[Dict[str, Any], str, Any]:
        """Make a call through the gateway. Returns dict (JSON), str (text), or full response."""
        pass

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True if the gateway handler is properly configured."""
        pass

    @abstractmethod
    def get_provider_name(self) -> str:
        """Return the name of the gateway provider (e.g. vercel)."""
        pass
