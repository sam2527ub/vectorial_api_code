"""
Direct API Client - Fallback for direct API calls when gateway is disabled.

This file provides a fallback mechanism for making direct API calls to OpenAI and Claude
when the AI gateway is disabled or unavailable. It uses the official AsyncOpenAI and
AsyncAnthropic SDKs to make direct API calls, bypassing the gateway entirely. This ensures
the application can still function even when the gateway is not configured or enabled.
"""
import json
import os
from typing import List, Dict, Any, Optional
from fastapi import HTTPException
from openai import AsyncOpenAI
from anthropic import AsyncAnthropic
from app.services.ai_gateway_service.utils import gateway_utils as utils


class DirectApiClient:
    """Fallback for direct API calls when gateway is disabled. Uses async SDK clients."""
    
    @staticmethod
    async def call_openai(profile_id: str, messages: List[Dict[str, str]], max_tokens: int, model: Optional[str]) -> Dict[str, Any]:
        """Call OpenAI API directly without gateway using AsyncOpenAI SDK. Returns parsed JSON response."""
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise HTTPException(status_code=503, detail="OPENAI_API_KEY not configured")
        
        client = AsyncOpenAI(api_key=api_key)
        response = await client.chat.completions.create(
            model=model or "gpt-5-mini",
            messages=messages,
            max_completion_tokens=max_tokens,
            temperature=0.3
        )
        return json.loads(response.choices[0].message.content)
    
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
