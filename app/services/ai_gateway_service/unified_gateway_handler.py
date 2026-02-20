"""Unified Gateway Handler - Handles all AI models (OpenAI, Anthropic, Groq) through unified /v1 endpoint."""
import json
from typing import List, Dict, Any, Optional, Union
from openai import AsyncOpenAI, APIError, RateLimitError, APIConnectionError, APITimeoutError
from app.config import logger
from app.services.ai_gateway_service.utils import gateway_utils as utils
from app.services.openai_service.direct_api import parse_json_response


class UnifiedGatewayHandler:
    """Unified handler for all AI models via gateway's /v1 endpoint with model rotation."""
    
    def __init__(self, provider: str, openai_base_url: str, anthropic_base_url: str, base_url: str, api_key: str):
        """Initialize handler with provider, URLs, API key. Creates AsyncOpenAI client for gateway."""
        self.provider = provider
        self.openai_base_url = openai_base_url
        self.anthropic_base_url = anthropic_base_url
        self.base_url = base_url
        self.api_key = api_key
        
        # SDK handles retries automatically for transient errors
        self.client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.openai_base_url,
            timeout=120.0,
            max_retries=2
        )
    
    async def call(
        self,
        profile_id: str,
        messages: List[Dict[str, str]],
        max_tokens: int,
        model: str,
        fallback_models: Optional[List[str]] = None,
        provider_order: Optional[List[str]] = None,
        validate_summary: bool = False,
        return_text: bool = False
    ) -> Union[Dict[str, Any], str]:
        """
        Unified handler for all AI models via gateway's /v1 endpoint.
        Supports model rotation, provider routing, and works for OpenAI, Anthropic, Groq models.
        """
        normalized_model = utils.normalize_model_name(model, self.provider)
        normalized_fallbacks = [
            utils.normalize_model_name(m, self.provider) 
            for m in (fallback_models or [])
        ]
        
        try:
            request_params = {
                "model": normalized_model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": 0.3,
            }
            
            # Build providerOptions for gateway-managed model rotation
            gateway_options = {}
            if normalized_fallbacks:
                gateway_options["models"] = normalized_fallbacks
            if provider_order:
                gateway_options["order"] = provider_order
            
            if gateway_options:
                request_params["extra_body"] = {
                    "providerOptions": {
                        "gateway": gateway_options
                    }
                }
            
            response = await self.client.chat.completions.create(**request_params)
            
            if not response.choices or not response.choices[0].message.content:
                raise ValueError("Gateway returned empty response")
            
            content = response.choices[0].message.content.strip()
            actual_model = getattr(response, 'model', normalized_model)
            
            # Handle text responses (for Claude models)
            if return_text:
                logger.info(f"Profile {profile_id}: Success with model {actual_model}")
                return content
            
            # Handle JSON responses (for OpenAI/Groq models)
            # Use robust JSON parsing to handle malformed responses
            result = parse_json_response(content)
            
            # Validate summary if requested
            if validate_summary and not result.get("summary", "").strip():
                raise ValueError("Empty summary")
            
            logger.info(f"Profile {profile_id}: Success with model {actual_model}")
            return result
            
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"Profile {profile_id}: Validation error after gateway rotation: {e}")
            raise
            
        except (RateLimitError, APIError, APIConnectionError, APITimeoutError) as e:
            logger.error(f"Profile {profile_id}: Gateway failed after retries and model rotation ({type(e).__name__}): {e}")
            raise
            
        except Exception as e:
            logger.error(f"Profile {profile_id}: Unexpected error: {e}")
            raise
