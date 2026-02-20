"""Direct API handlers for OpenAI and Claude (fallback when gateway disabled). Part of OpenAI service."""
import json
import re
from typing import List, Dict, Any, Optional
from fastapi import HTTPException
from app.config import openai_client, anthropic_client, logger


def clean_json_content(content: str) -> str:
    """
    Clean JSON content by removing markdown code blocks and fixing formatting.
    Enhanced to handle malformed JSON from OpenAI.

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


def fix_malformed_json(content: str) -> str:
    """
    Attempt to fix common JSON malformation issues.

    Handles:
    - Unterminated strings
    - Invalid escape sequences
    - Truncated JSON
    - Unescaped newlines in strings

    Args:
        content: Potentially malformed JSON string

    Returns:
        Fixed JSON string (best effort)
    """
    if not content or not content.strip():
        return content

    # Try to find the last complete JSON object by matching braces
    brace_count = 0
    last_valid_pos = -1
    for i, char in enumerate(content):
        if char == '{':
            brace_count += 1
        elif char == '}':
            brace_count -= 1
            if brace_count == 0:
                last_valid_pos = i
        elif brace_count < 0:
            break

    if last_valid_pos > 0:
        potential_json = content[:last_valid_pos + 1]

        def fix_string_escapes(text):
            result = []
            i = 0
            in_string = False
            escape_next = False
            while i < len(text):
                char = text[i]
                if escape_next:
                    result.append(char)
                    escape_next = False
                elif char == '\\':
                    result.append('\\')
                    escape_next = True
                elif char == '"' and not escape_next:
                    in_string = not in_string
                    result.append('"')
                elif in_string:
                    if char == '\n':
                        result.append('\\n')
                    elif char == '\r':
                        result.append('\\r')
                    elif char == '\t':
                        result.append('\\t')
                    elif char == '\b':
                        result.append('\\b')
                    elif char == '\f':
                        result.append('\\f')
                    else:
                        result.append(char)
                else:
                    result.append(char)
                i += 1
            return ''.join(result)

        try:
            json.loads(potential_json)
            return potential_json
        except json.JSONDecodeError:
            fixed = fix_string_escapes(potential_json)
            try:
                json.loads(fixed)
                return fixed
            except json.JSONDecodeError:
                pass
        return potential_json

    return content


def parse_json_response(content: str) -> Dict[str, Any]:
    """
    Parse JSON response from string, with robust error handling and multiple strategies.

    Args:
        content: JSON string (may contain markdown or be malformed)

    Returns:
        Parsed JSON dictionary

    Raises:
        ValueError: If JSON cannot be parsed after all attempts
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
        logger.debug(f"Initial JSON parse failed: {e}")

    fixed_content = fix_malformed_json(cleaned_content)
    try:
        return json.loads(fixed_content)
    except json.JSONDecodeError as e:
        logger.debug(f"Fixed JSON parse failed: {e}")

    brace_count = 0
    last_valid_pos = -1
    for i, char in enumerate(cleaned_content):
        if char == '{':
            brace_count += 1
        elif char == '}':
            brace_count -= 1
            if brace_count == 0:
                last_valid_pos = i
        elif brace_count < 0:
            break

    if last_valid_pos > 0:
        truncated_json = cleaned_content[:last_valid_pos + 1]
        try:
            return json.loads(truncated_json)
        except json.JSONDecodeError as e:
            logger.debug(f"Truncated JSON parse failed: {e}")

    try:
        result = {}
        summary_pattern = r'"summary"\s*:\s*"((?:[^"\\]|\\.)*)"'
        summary_match = re.search(summary_pattern, cleaned_content, re.DOTALL)
        if not summary_match:
            unterminated_pattern = r'"summary"\s*:\s*"([^"]*)'
            summary_match = re.search(unterminated_pattern, cleaned_content, re.DOTALL)
        if summary_match:
            summary_text = summary_match.group(1)
            summary_text = summary_text.replace('\\n', '\n').replace('\\r', '\r').replace('\\t', '\t')
            summary_text = summary_text.replace('\\"', '"').replace('\\\\', '\\')
            result['summary'] = summary_text
        highlights_pattern = r'"highlights"\s*:\s*\[(.*?)\]'
        highlights_match = re.search(highlights_pattern, cleaned_content, re.DOTALL)
        if highlights_match:
            highlights_str = highlights_match.group(1)
            highlight_items = re.findall(r'"((?:[^"\\]|\\.)*)"', highlights_str)
            result['highlights'] = [item.replace('\\"', '"').replace('\\\\', '\\') for item in highlight_items]
        keywords_pattern = r'"keywords"\s*:\s*\[(.*?)\]'
        keywords_match = re.search(keywords_pattern, cleaned_content, re.DOTALL)
        if keywords_match:
            keywords_str = keywords_match.group(1)
            keyword_items = re.findall(r'"((?:[^"\\]|\\.)*)"', keywords_str)
            result['keywords'] = [item.replace('\\"', '"').replace('\\\\', '\\') for item in keyword_items]
        if result:
            logger.warning(f"Extracted partial JSON using regex fallback: {list(result.keys())}")
            if 'summary' not in result:
                result['summary'] = ''
            if 'highlights' not in result:
                result['highlights'] = []
            if 'keywords' not in result:
                result['keywords'] = []
            return result
    except Exception as e:
        logger.debug(f"Regex extraction failed: {e}")

    raise ValueError(
        f"Invalid JSON format after multiple parsing attempts. "
        f"Content preview: {cleaned_content[:200]}..."
    )


class DirectAPIHandler:
    """Handles direct API calls when gateway is disabled."""

    async def call_openai_direct(
        self,
        profile_id: str,
        messages: List[Dict[str, str]],
        max_tokens: int,
        validate_summary: bool
    ) -> Dict[str, Any]:
        """Call OpenAI API directly (bypassing gateway)."""
        if not openai_client:
            raise HTTPException(
                status_code=503,
                detail="OpenAI client not initialized. Please check OPENAI_API_KEY."
            )
        try:
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
        """Call Claude API directly (bypassing gateway)."""
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
