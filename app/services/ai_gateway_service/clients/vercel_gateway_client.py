"""Vercel AI Gateway client (vectorial-reddit-pipeline style)."""
import json
from typing import Any, Dict, List, Optional, Union

from openai import AsyncOpenAI, APIError, RateLimitError, APIConnectionError, APITimeoutError

from app.config import logger

from ..utils import gateway_utils as utils
from .interface import GatewayHandlerInterface


class VercelGatewayClient(GatewayHandlerInterface):
    """Unified handler for all AI models via Vercel gateway /v1 endpoint."""

    def __init__(self, openai_base_url: str, anthropic_base_url: str, api_key: str):
        self.openai_base_url = openai_base_url
        self.anthropic_base_url = anthropic_base_url
        self.api_key = api_key
        self.provider = "vercel"
        self.client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.openai_base_url,
            timeout=120.0,
            max_retries=2,
        )

    def is_configured(self) -> bool:
        return bool(self.api_key and self.openai_base_url)

    def get_provider_name(self) -> str:
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
        response_format: Optional[Dict[str, str]] = None,
    ) -> Union[Dict[str, Any], str, Any]:
        normalized_model = utils.normalize_model_name(model, self.provider)
        normalized_fallbacks = [
            utils.normalize_model_name(m, self.provider) for m in (fallback_models or [])
        ]
        request_params = {
            "model": normalized_model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.3,
        }
        gateway_options = {}
        if normalized_fallbacks:
            gateway_options["models"] = normalized_fallbacks
        if provider_order:
            gateway_options["order"] = provider_order
        if gateway_options:
            request_params["extra_body"] = {"providerOptions": {"gateway": gateway_options}}

        response = await self.client.chat.completions.create(**request_params)
        if not response.choices or not response.choices[0].message.content:
            raise ValueError("Gateway returned empty response")

        content = response.choices[0].message.content.strip()
        actual_model = getattr(response, "model", normalized_model)

        if return_text:
            logger.info(f"Profile {profile_id}: Success with model {actual_model}")
            return content

        result = utils.parse_json_response(content, logger=logger)
        if validate_summary and not result.get("summary", "").strip():
            raise ValueError("Empty summary")
        logger.info(f"Profile {profile_id}: Success with model {actual_model}")
        return result
