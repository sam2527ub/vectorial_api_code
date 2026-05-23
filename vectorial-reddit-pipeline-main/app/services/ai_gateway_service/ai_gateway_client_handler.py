"""AI Gateway Client - Routes AI API requests through gateway."""
from typing import List, Dict, Any, Optional, Union, Callable

from openai import APIError, RateLimitError, APIConnectionError, APITimeoutError

from app.config import logger
from app.services.ai_gateway_service.config import GatewayConfig
from app.services.ai_gateway_service.clients.factory import create_gateway_handler
from app.services.ai_gateway_service.clients.interface import GatewayHandlerInterface
from app.services.ai_gateway_service.direct_api_fallback_handler import DirectApiClient
from app.services.ai_gateway_service.utils.gateway_utils import get_model_provider

class AIGatewayClient:
    """Routes AI API requests through gateway (rotation handled by gateway)."""
    
    def __init__(self, config: Optional[GatewayConfig] = None):
        """Initialize gateway client."""
        self.config = config or GatewayConfig()
        self.provider = self.config.provider
        self.api_key = self.config.api_key
        
        self._setup_urls()
        self.enabled = self._check_if_enabled()
        
        # Use factory to create appropriate gateway handler based on provider
        if self.enabled:
            try:
                self.gateway_handler: Optional[GatewayHandlerInterface] = create_gateway_handler(
                    provider=self.provider,
                    openai_base_url=self.openai_base_url,
                    anthropic_base_url=self.anthropic_base_url,
                    base_url=getattr(self, 'base_url', None),
                    api_key=self.api_key
                )
            except NotImplementedError as e:
                logger.error(f"Gateway provider '{self.provider}' not implemented: {e}")
                self.gateway_handler = None
                self.enabled = False
        else:
            self.gateway_handler = None
        
        self._log_initialization()
    
    def _setup_urls(self):
        """Configure gateway URLs based on provider."""
        if self.provider == "vercel":
            self.openai_base_url = self.config.base_url.rstrip("/") or self.config.vercel_openai_url
            self.anthropic_base_url = self.config.base_url.rstrip("/") or self.config.vercel_anthropic_url
            if self.anthropic_base_url.endswith("/v1"):
                self.anthropic_base_url = self.anthropic_base_url[:-3]
        else:
            self.base_url = self.config.base_url.rstrip("/")
            self.openai_base_url = self.base_url
            self.anthropic_base_url = self.base_url
    
    def _check_if_enabled(self) -> bool:
        """Check if gateway is enabled and configured."""
        if self.provider == "vercel":
            return self.config.use_gateway and bool(self.api_key)
        return self.config.use_gateway and bool(self.config.base_url) and bool(self.api_key)
    
    def _log_initialization(self):
        """Log gateway initialization status."""
        if self.enabled:
            if self.provider == "vercel":
                logger.info(f"Vercel AI Gateway enabled: {self.openai_base_url}")
            else:
                logger.info(f"AI Gateway enabled: {self.base_url} (provider: {self.provider})")
        else:
            logger.info("AI Gateway disabled - using direct API calls")
    
    async def call_via_gateway(
        self,
        context_id: str,
        messages: List[Dict[str, str]],
        max_tokens: int,
        model: Optional[str] = None,
        default_model: Optional[str] = None,
        fallback_models: Optional[List[str]] = None,
        config_default_attr: str = "profile_summary_default",
        config_fallbacks_attr: str = "profile_summary_fallbacks",
        hardcoded_default: str = "openai/gpt-5-mini",
        validate_summary: bool = False,
        return_text: bool = False,
        legacy_model_handler: Optional[Callable[[Optional[str], str], Optional[str]]] = None,
        direct_api_fallback_model: Optional[str] = None,
        logprobs: Optional[bool] = False,
        top_logprobs: Optional[int] = None,
        return_full_response: bool = False,
        response_format: Optional[Dict[str, str]] = None
    ) -> Union[Dict[str, Any], str, Any]:
        """Unified gateway call for all AI models."""
        if not self.enabled:
            # Fallback to direct API calls (OpenAI, Anthropic, or Groq based on model)
            fallback_model = direct_api_fallback_model or model or hardcoded_default
            provider = get_model_provider(fallback_model)
            if provider == "groq":
                return await DirectApiClient.call_groq(
                    context_id, messages, max_tokens, fallback_model,
                    return_full_response=return_full_response,
                    response_format=response_format
                )
            if return_text:
                return await DirectApiClient.call_claude(context_id, messages, max_tokens, fallback_model)
            return await DirectApiClient.call_openai(
                context_id, messages, max_tokens, fallback_model,
                logprobs=logprobs, top_logprobs=top_logprobs, return_full_response=return_full_response,
                response_format=response_format
            )
        if default_model is None:
            default_model = getattr(self.config, config_default_attr, None) or hardcoded_default

        if fallback_models is None:
            fallback_models = (
                getattr(self.config, config_fallbacks_attr, [])
                if hasattr(self.config, config_fallbacks_attr)
                else []
            )

        primary_candidate = model or default_model
        models_ordered: List[str] = []
        for m in [primary_candidate, *fallback_models]:
            if m and m not in models_ordered:
                models_ordered.append(m)

        if not models_ordered:
            raise ValueError("At least one model must be resolved for gateway call")

        primary_model = models_ordered[0]
        fallback_models_final = models_ordered[1:]

        logger.debug(
            f"Context {context_id}: Primary model '{primary_model}' with "
            f"fallbacks {fallback_models_final} (provider={self.provider})"
        )

        if not self.gateway_handler:
            raise RuntimeError("Gateway handler not initialized. Gateway may be disabled or misconfigured.")

        try:
            result = await self.gateway_handler.call(
                profile_id=context_id,
                messages=messages,
                max_tokens=max_tokens,
                model=primary_model,
                fallback_models=fallback_models_final,
                validate_summary=validate_summary,
                return_text=return_text,
                logprobs=logprobs,
                top_logprobs=top_logprobs,
                return_full_response=return_full_response,
                response_format=response_format
            )
        except (RateLimitError, APIError, APIConnectionError, APITimeoutError, Exception) as e:
            logger.error(
                "Context %s: Gateway call failed with %s: %s. Falling back to direct API.",
                context_id,
                type(e).__name__,
                e,
            )
            fallback_model = direct_api_fallback_model or model or default_model or hardcoded_default
            provider = get_model_provider(fallback_model)

            if provider == "groq":
                return await DirectApiClient.call_groq(
                    context_id,
                    messages,
                    max_tokens,
                    fallback_model,
                    return_full_response=return_full_response,
                    response_format=response_format,
                )
            if return_text:
                return await DirectApiClient.call_claude(
                    context_id,
                    messages,
                    max_tokens,
                    fallback_model,
                )
            return await DirectApiClient.call_openai(
                context_id,
                messages,
                max_tokens,
                fallback_model,
                logprobs=logprobs,
                top_logprobs=top_logprobs,
                return_full_response=return_full_response,
                response_format=response_format,
            )

        if return_full_response:
            return result
        elif return_text:
            return result if isinstance(result, str) else str(result)
        else:
            return result if isinstance(result, dict) else result
