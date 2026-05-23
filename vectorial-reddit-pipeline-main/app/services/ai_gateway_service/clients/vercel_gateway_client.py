
import json
from typing import List, Dict, Any, Optional, Union
from openai import AsyncOpenAI, APIError, RateLimitError, APIConnectionError, APITimeoutError
from app.config import logger
from app.services.ai_gateway_service.clients.interface import GatewayHandlerInterface
from app.services.ai_gateway_service.utils import gateway_utils as utils


class VercelGatewayClient(GatewayHandlerInterface):
    """Vercel AI Gateway implementation."""
    
    def __init__(self, openai_base_url: str, anthropic_base_url: str, api_key: str):
        """
        Initialize Vercel gateway client.
        """
        self.openai_base_url = openai_base_url
        self.anthropic_base_url = anthropic_base_url
        self.api_key = api_key
        self.provider = "vercel"
        
        # SDK handles retries automatically for transient errors
        self.client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.openai_base_url,
            timeout=120.0,
            max_retries=2
        )
    
    def is_configured(self) -> bool:
        """Check if Vercel gateway is properly configured."""
        return bool(self.api_key and self.openai_base_url)
    
    def get_provider_name(self) -> str:
        """Return provider name."""
        return self.provider
    
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
        Unified handler for all AI models via Vercel gateway's /v1 endpoint.
        Supports model rotation, provider routing, and works for OpenAI, Anthropic, Groq models.
        Logprobs are only applied for OpenAI models.
        """
        normalized_model = utils.normalize_model_name(model, self.provider)
        normalized_fallbacks = [
            utils.normalize_model_name(m, self.provider) 
            for m in (fallback_models or [])
        ]
        
        # Check if model is OpenAI (logprobs only supported for OpenAI)
        model_provider = utils.get_model_provider(normalized_model)
        is_openai = model_provider == "openai"
        
        # Only apply logprobs for OpenAI models
        should_use_logprobs = logprobs and is_openai
        
        try:
            request_params = {
                "model": normalized_model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": 0.3,
            }
            
            # Add logprobs only for OpenAI models
            if should_use_logprobs:
                request_params["logprobs"] = True
                if top_logprobs is not None:
                    request_params["top_logprobs"] = top_logprobs
            
            # Do not pass response_format to Vercel AI Gateway - it returns 400 Invalid input.
            # Rely on prompt instructions for JSON output; fallback parsing handles non-JSON.

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
            
            # If full response is requested (for logprobs), return the full response object
            if return_full_response:
                actual_model = getattr(response, 'model', normalized_model)
                logger.info(f"Profile {profile_id}: Success with model {actual_model} (returning full response)")
                return response
            
            content = response.choices[0].message.content.strip()
            actual_model = getattr(response, 'model', normalized_model)
            
            if return_text:
                logger.info(f"Profile {profile_id}: Success with model {actual_model}")
                return content
            
            result = utils.parse_json_response(content, logger=logger)
            
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
