from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Union


class GatewayHandlerInterface(ABC):
    """Interface for gateway handlers (Vercel, Portkey, Langfuse, etc.)."""
    
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
        response_format: Optional[Dict[str, str]] = None
    ) -> Union[Dict[str, Any], str, Any]:
        """
        Make a call through the gateway.
        
        Args:
            profile_id: Identifier for logging/tracking
            messages: List of message dicts with 'role' and 'content'
            max_tokens: Maximum tokens in response
            model: Primary model to use
            fallback_models: List of fallback models for rotation
            provider_order: Optional provider preference order
            validate_summary: Whether to validate summary in response
            return_text: If True, return raw text; if False, parse as JSON
            logprobs: Whether to return logprobs (only supported for OpenAI models)
            top_logprobs: Number of top logprobs to return (only for OpenAI models)
            return_full_response: If True, return full response object (needed for logprobs)
            response_format: Optional response format dict (e.g., {"type": "json_object"})
            
        Returns:
            Dict[str, Any] if return_text=False and return_full_response=False
            str if return_text=True
            Full response object if return_full_response=True
            
        Raises:
            ValueError: If response validation fails
            APIError: If gateway API call fails
        """
        pass
    
    @abstractmethod
    def is_configured(self) -> bool:
        """
        Return True if the gateway handler is properly configured.
        
        Returns:
            True if gateway is configured and ready to use
        """
        pass
    
    @abstractmethod
    def get_provider_name(self) -> str:
        """
        Return the name of the gateway provider.
        
        Returns:
            Provider name (e.g., 'vercel', 'portkey', 'langfuse')
        """
        pass
