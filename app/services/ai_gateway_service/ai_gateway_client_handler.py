"""AI Gateway Client - Routes AI API requests through gateway."""
from typing import List, Dict, Any, Optional, Union, Callable
from app.config import logger
from app.services.ai_gateway_service.config import GatewayConfig
from app.services.ai_gateway_service.unified_gateway_handler import UnifiedGatewayHandler
from app.services.ai_gateway_service.direct_api_fallback_handler import DirectApiClient
from app.services.ai_gateway_service import model_rotation_handler as model_rotation


class AIGatewayClient:
    """Routes AI API requests through gateway with cross-provider model rotation."""
    
    def __init__(self, config: Optional[GatewayConfig] = None):
        """Initialize gateway client."""
        self.config = config or GatewayConfig()
        self.provider = self.config.provider
        self.api_key = self.config.api_key
        
        self._setup_urls()
        self.enabled = self._check_if_enabled()
        
        self.unified_handler = UnifiedGatewayHandler(
            self.provider,
            self.openai_base_url,
            self.anthropic_base_url,
            getattr(self, 'base_url', None),
            self.api_key
        )
        
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
            
            if len(self.config.model_fallbacks) > 1:
                logger.info(f"Model fallbacks configured: {len(self.config.model_fallbacks)} models")
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
        direct_api_fallback_model: Optional[str] = None
    ) -> Union[Dict[str, Any], str]:
        """
        Unified gateway call for all AI models.

        This function handles all AI model calls through the gateway, supporting both:
        - Profile summaries: return_text=False, config=profile_summary_*, returns JSON dict
        - Group summaries: return_text=True, config=group_summary_*, returns text string
        """
        if not self.enabled:
            # Fallback to direct API calls
            if return_text:
                fallback_model = direct_api_fallback_model or model or hardcoded_default
                return await DirectApiClient.call_claude(context_id, messages, max_tokens, fallback_model)
            else:
                fallback_model = direct_api_fallback_model or model or hardcoded_default
                return await DirectApiClient.call_openai(context_id, messages, max_tokens, fallback_model)
        
        # Resolve model configuration
        resolved_default, resolved_fallbacks = model_rotation.resolve_model_config(
            config=self.config,
            default_model=default_model,
            fallback_models=fallback_models,
            config_default_attr=config_default_attr,
            config_fallbacks_attr=config_fallbacks_attr,
            hardcoded_default=hardcoded_default
        )
        
        # Prepare model rotation with optional legacy handler
        primary_model, fallback_models_final = model_rotation.prepare_model_rotation(
            model=model,
            default_model=resolved_default,
            fallback_models=resolved_fallbacks,
            provider=self.provider,
            legacy_model_handler=legacy_model_handler
        )
        
        model_rotation.log_model_rotation(context_id, primary_model, fallback_models_final)
        
        # Call unified handler
        result = await self.unified_handler.call(
            profile_id=context_id,
            messages=messages,
            max_tokens=max_tokens,
            model=primary_model,
            fallback_models=fallback_models_final,
            validate_summary=validate_summary,
            return_text=return_text
        )
        
        # Ensure correct return type
        if return_text:
            return result if isinstance(result, str) else str(result)
        else:
            return result if isinstance(result, dict) else result
