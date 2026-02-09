"""Direct API handlers for OpenAI and Claude (fallback when gateway disabled)."""
import json
from typing import List, Dict, Any
from fastapi import HTTPException
from app.config import openai_client, anthropic_client, logger


def clean_json_content(content: str) -> str:
    """
    Clean JSON content by removing markdown code blocks and fixing formatting.
    
    Args:
        content: Raw content string that may contain markdown or formatting issues
        
    Returns:
        Cleaned JSON string
    """
    content = content.strip()
    if not content:
        return content
    
    # Remove markdown code blocks
    if '```json' in content:
        start_idx = content.find('```json') + 7
        end_idx = content.find('```', start_idx)
        if end_idx != -1:
            content = content[start_idx:end_idx].strip()
    elif '```' in content:
        start_idx = content.find('```') + 3
        end_idx = content.find('```', start_idx)
        if end_idx != -1:
            content = content[start_idx:end_idx].strip()
    
    # Fix JSON formatting issues
    if not content.startswith('{'):
        content_stripped = content.strip()
        if content_stripped.startswith('"') and not content_stripped.startswith('{"'):
            content = '{' + content_stripped
            if not content.endswith('}'):
                content = content + '}'
    
    return content


def parse_json_response(content: str) -> Dict[str, Any]:
    """
    Parse JSON response from string, with cleaning.
    
    Args:
        content: JSON string (may contain markdown)
        
    Returns:
        Parsed JSON dictionary
        
    Raises:
        ValueError: If JSON is invalid
    """
    cleaned_content = clean_json_content(content)
    
    if not cleaned_content.startswith('{'):
        raise ValueError(
            f"Response is not valid JSON. Expected object starting with {{, "
            f"but got: {cleaned_content[:100]}"
        )
    
    try:
        return json.loads(cleaned_content)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON format: {e}") from e


class DirectAPIHandler:
    """Handles direct API calls when gateway is disabled."""
    
    async def call_openai_direct(
        self,
        profile_id: str,
        messages: List[Dict[str, str]],
        max_tokens: int,
        validate_summary: bool
    ) -> Dict[str, Any]:
        """
        Call OpenAI API directly (bypassing gateway).
        
        Args:
            profile_id: Profile identifier for logging
            messages: List of message dictionaries
            max_tokens: Maximum tokens for completion
            validate_summary: Whether to validate summary field
            
        Returns:
            Parsed JSON response dictionary
            
        Raises:
            HTTPException: If API call fails
        """
        if not openai_client:
            raise HTTPException(
                status_code=503,
                detail="OpenAI client not initialized. Please check OPENAI_API_KEY."
            )
        
        try:
            # o3-mini model requires max_completion_tokens instead of max_tokens
            completion = openai_client.chat.completions.create(
                model="o3-mini",
                messages=messages,
                max_completion_tokens=max_tokens,
                response_format={"type": "json_object"}
            )
            
            if not completion.choices or not completion.choices[0].message.content:
                raise ValueError("OpenAI returned empty response")
            
            content = completion.choices[0].message.content.strip()
            if not content:
                raise ValueError("OpenAI returned empty content")
            
            result = parse_json_response(content)
            
            if validate_summary and not result.get("summary", "").strip():
                raise ValueError("OpenAI returned empty summary")
            
            return result
        except Exception as e:
            logger.error(f"Profile {profile_id}: Direct API call failed: {e}")
            raise
    
    async def call_claude_direct(
        self,
        context_id: str,
        messages: List[Dict[str, str]],
        max_tokens: int,
        model: str
    ) -> str:
        """
        Call Claude API directly (bypassing gateway).
        
        Args:
            context_id: Context identifier for logging
            messages: List of message dictionaries
            max_tokens: Maximum tokens for completion
            model: Model name
            
        Returns:
            Text response string
            
        Raises:
            HTTPException: If API call fails
        """
        if not anthropic_client:
            raise HTTPException(
                status_code=503,
                detail="Anthropic client not initialized. Please check ANTHROPIC_API_KEY."
            )
        
        system_message = None
        user_messages = []
        
        for msg in messages:
            if msg.get("role") == "system":
                system_message = msg.get("content", "")
            else:
                user_messages.append(msg)
        
        if not user_messages:
            raise ValueError("At least one non-system message is required for Claude API")
        
        try:
            claude_messages = [
                {"role": msg.get("role"), "content": msg.get("content", "")}
                for msg in user_messages
                if msg.get("role") in ["user", "assistant"]
            ]
            
            response = anthropic_client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system_message if system_message else None,
                messages=claude_messages,
                temperature=0.3
            )
            
            if response.content and len(response.content) > 0:
                return response.content[0].text
            raise ValueError("Claude returned empty response")
        except Exception as e:
            logger.error(f"Context {context_id}: Direct API call failed: {e}")
            raise
