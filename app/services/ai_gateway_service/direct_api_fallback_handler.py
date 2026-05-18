"""
Direct API Client - Fallback when gateway is disabled (vectorial-reddit-pipeline style).
"""
import asyncio
import os
from typing import List, Dict, Any, Optional

from fastapi import HTTPException
from openai import AsyncOpenAI
from anthropic import AsyncAnthropic
from groq import Groq

from app.config import logger as app_logger
from app.services.ai_gateway_service.utils import gateway_utils as utils


def _groq_model_name(model: Optional[str]) -> str:
    """Strip provider prefix for Groq (e.g. groq/llama-3.3-70b -> llama-3.3-70b)."""
    if not model:
        return "llama-3.3-70b-versatile"
    return model.split("/", 1)[-1] if "/" in model else model


class DirectApiClient:
    """Fallback for direct API calls when gateway is disabled."""

    @staticmethod
    async def call_openai(
        profile_id: str,
        messages: List[Dict[str, str]],
        max_tokens: int,
        model: Optional[str],
        validate_summary: bool = False,
        response_format: Optional[Dict[str, str]] = None,
        temperature: float = 0.3,
    ) -> Dict[str, Any]:
        """Call OpenAI API directly. Returns parsed JSON (robust parse_json_response)."""
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise HTTPException(status_code=503, detail="OPENAI_API_KEY not configured")
        client = AsyncOpenAI(api_key=api_key)
        rfmt = response_format if response_format is not None else {"type": "json_object"}
        response = await client.chat.completions.create(
            model=model or "gpt-5-mini",
            messages=messages,
            max_completion_tokens=max_tokens,
            temperature=temperature,
            response_format=rfmt,
        )
        content = (response.choices[0].message.content or "").strip()
        if not content:
            raise ValueError("OpenAI returned empty content")
        result = utils.parse_json_response(content, logger=app_logger)
        if validate_summary and not result.get("summary", "").strip():
            raise ValueError("OpenAI returned empty summary")
        return result
    
    @staticmethod
    async def call_claude(context_id: str, messages: List[Dict[str, str]], max_tokens: int, model: str) -> str:
        """Call Anthropic Claude API directly without gateway using AsyncAnthropic SDK."""
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
    ) -> Dict[str, Any]:
        """Call Groq API directly. Returns parsed JSON."""
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise HTTPException(status_code=503, detail="GROQ_API_KEY not configured")
        groq_model = _groq_model_name(model)
        client = Groq(api_key=api_key)

        def _create():
            return client.chat.completions.create(
                model=groq_model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=0.1,
                response_format={"type": "json_object"},
            )

        response = await asyncio.to_thread(_create)
        if not response.choices or not response.choices[0].message.content:
            raise ValueError("Groq returned empty response")
        content = response.choices[0].message.content.strip()
        return utils.parse_json_response(content, logger=app_logger)
