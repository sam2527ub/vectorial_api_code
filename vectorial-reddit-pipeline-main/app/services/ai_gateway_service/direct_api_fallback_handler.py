"""
Direct API Client - Fallback for direct API calls when gateway is disabled.
"""
import asyncio
import json
import os
from typing import List, Dict, Any, Optional, Union
from fastapi import HTTPException
from openai import AsyncOpenAI
from anthropic import AsyncAnthropic
from groq import Groq
from app.config import logger
from app.services.ai_gateway_service.utils import gateway_utils as utils


def _groq_model_name(model: Optional[str]) -> str:
    """Strip provider prefix for Groq API (e.g. groq/llama-3.3-70b-versatile -> llama-3.3-70b-versatile)."""
    if not model:
        return "llama-3.3-70b-versatile"
    return model.split("/", 1)[-1] if "/" in model else model


class DirectApiClient:
    """Fallback for direct API calls when gateway is disabled. Uses async SDK clients."""
    
    @staticmethod
    async def call_openai(
        profile_id: str,
        messages: List[Dict[str, str]],
        max_tokens: int,
        model: Optional[str],
        logprobs: Optional[bool] = False,
        top_logprobs: Optional[int] = None,
        return_full_response: bool = False,
        response_format: Optional[Dict[str, str]] = None
    ) -> Union[Dict[str, Any], Any]:
        """Call OpenAI API directly without gateway. Returns parsed JSON response or full response object."""
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise HTTPException(status_code=503, detail="OPENAI_API_KEY not configured")
        
        client = AsyncOpenAI(api_key=api_key)
        request_params = {
            "model": model or "gpt-5-mini",
            "messages": messages,
            "max_completion_tokens": max_tokens,
            "temperature": 0.3
        }
        
        # Add logprobs only if requested (OpenAI models support logprobs)
        if logprobs:
            request_params["logprobs"] = True
            if top_logprobs is not None:
                request_params["top_logprobs"] = top_logprobs
        
        # Add response format if specified (e.g., JSON mode)
        if response_format:
            request_params["response_format"] = response_format
        
        response = await client.chat.completions.create(**request_params)
        
        # Return full response if requested (for logprobs extraction)
        if return_full_response:
            return response
        
        return json.loads(response.choices[0].message.content)
    
    @staticmethod
    async def call_claude(context_id: str, messages: List[Dict[str, str]], max_tokens: int, model: str) -> str:
        """Call Anthropic Claude API directly without gateway."""
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY not configured")
        
        client = AsyncAnthropic(api_key=api_key)
        system_message, claude_messages = utils.prepare_claude_messages(messages)
        
        response = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_message if system_message else None,
            messages=claude_messages,
            temperature=0.3
        )
        
        if response.content and len(response.content) > 0:
            return response.content[0].text
        raise ValueError("Claude returned empty response")

    @staticmethod
    async def call_groq(
        profile_id: str,
        messages: List[Dict[str, str]],
        max_tokens: int,
        model: Optional[str],
        return_full_response: bool = False,
        response_format: Optional[Dict[str, str]] = None
    ) -> Union[Dict[str, Any], Any]:
        """Call Groq API directly without gateway. Returns parsed JSON response or full response object."""
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise HTTPException(status_code=503, detail="GROQ_API_KEY not configured")

        groq_model = _groq_model_name(model)
        client = Groq(api_key=api_key)

        request_params = {
            "model": groq_model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.1,
        }
        if response_format:
            request_params["response_format"] = response_format

        def _create():
            return client.chat.completions.create(**request_params)

        response = await asyncio.to_thread(_create)

        if not response.choices or not response.choices[0].message.content:
            raise ValueError("Groq returned empty response")

        if return_full_response:
            return response

        content = response.choices[0].message.content.strip()
        return utils.parse_json_response(content, logger=logger)
