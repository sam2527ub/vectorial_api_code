"""
AI Gateway Utilities - Helper functions for message formatting and model name normalization.

This file provides utility functions used across the AI Gateway module:
- Model name normalization (adds provider prefixes for Vercel gateway)
- Message formatting for Claude API (extracts system messages, formats user/assistant messages)
- Model provider detection (identifies which provider a model belongs to)
- Robust JSON parsing for AI responses (handles malformed JSON)
These utilities ensure consistent message and model name formatting across different providers.
"""
import json
import re
from typing import List, Dict, Any, Optional, Tuple


# Model Provider Detection

def get_model_provider(model: str) -> str:
    """Extract provider from model name. Returns 'openai', 'anthropic', 'groq', or 'unknown'."""
    if "/" in model:
        provider = model.split("/")[0].lower()
        if provider in ["openai", "anthropic", "groq", "google", "vertex", "bedrock"]:
            return provider
    
    # Detect provider by model name pattern if no prefix
    model_lower = model.lower()
    if model_lower.startswith("claude"):
        return "anthropic"
    elif model_lower.startswith("gpt") or model_lower.startswith("o1"):
        return "openai"
    elif model_lower.startswith("llama") or model_lower.startswith("mixtral") or model_lower.startswith("gemma"):
        return "groq"
    
    # Default to openai if no provider prefix (for backward compatibility)
    return "openai"


def normalize_model_name(model: str, provider: str) -> str:
    """Normalize model name. Vercel adds provider prefix if missing, others return as-is."""
    if provider == "vercel" and "/" not in model:
        detected_provider = get_model_provider(model)
        return f"{detected_provider}/{model}"
    return model


def prepare_claude_messages(messages: List[Dict[str, str]]) -> Tuple[Optional[str], List[Dict[str, str]]]:
    """Extract system message and return (system_message, user/assistant_messages)."""
    system_message = None
    claude_messages = []
    
    for msg in messages:
        if msg.get("role") == "system":
            system_message = msg.get("content", "")
        elif msg.get("role") in ["user", "assistant"]:
            claude_messages.append({
                "role": msg.get("role"),
                "content": msg.get("content", "")
            })
    
    return system_message, claude_messages


def parse_json_response(content: str, logger=None) -> Dict[str, Any]:
    """
    Robustly parse JSON from AI response, handling common issues:
    - Markdown code blocks
    - Unescaped quotes in strings
    - Truncated JSON
    - Extra text before/after JSON
    
    Args:
        content: Raw content string from AI response
        logger: Optional logger instance for warning/error messages
        
    Returns:
        Parsed JSON as dictionary
        
    Raises:
        json.JSONDecodeError: If JSON cannot be parsed after all attempts
    """
    original_content = content
    
    # Remove markdown code blocks if present
    if content.startswith("```"):
        lines = content.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines).strip()
    
    # Try to extract JSON object from content (in case there's extra text)
    json_match = re.search(r'\{.*\}', content, re.DOTALL)
    if json_match:
        content = json_match.group(0)
    
    # Try standard JSON parsing first
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        # Log raw content for debugging (truncated to avoid log spam)
        if logger:
            preview = original_content[:800] + ("..." if len(original_content) > 800 else "")
            logger.warning(
                f"JSON parse failed: {e}. Content preview (len={len(original_content)}): {preview}"
            )
            logger.warning("Attempting to fix common issues...")

        # Strategy 0: "Expecting value" near end often means truncation - try to close and parse
        err_msg = str(e).lower()
        if "expecting value" in err_msg:
            error_pos = getattr(e, "pos", len(content))
            # If error is in last 50 chars, might be truncation - try closing open structures
            if error_pos >= len(content) - 50:
                for suffix in ['""}', '"}', "]}", "}"]:
                    try:
                        fixed = content[:error_pos] + suffix
                        return json.loads(fixed)
                    except json.JSONDecodeError:
                        pass

        # Strategy 1: Try to fix unterminated strings by finding and closing them
        # Look for string values that might be unterminated
        try:
            # Find the position of the error
            error_pos = getattr(e, 'pos', len(content))
            
            # Try to find and fix unterminated strings
            # Look backwards from error position to find the start of the problematic string
            lines = content[:error_pos].split('\n')
            if len(lines) > 0:
                # Check if we're in the middle of a string
                line_before_error = lines[-1]
                
                # Count unescaped quotes in this line
                quote_count = 0
                i = 0
                while i < len(line_before_error):
                    if line_before_error[i] == '\\':
                        i += 2  # Skip escaped character
                        continue
                    if line_before_error[i] == '"':
                        quote_count += 1
                    i += 1
                
                # If odd number of quotes, string might be unterminated
                # Try to close it and continue parsing
                if quote_count % 2 == 1:
                    # Try to find where the string should end
                    # Look for the next comma, }, or ] after the error position
                    remaining_content = content[error_pos:]
                    end_match = re.search(r'[,}\]]', remaining_content)
                    if end_match:
                        # Insert closing quote before the delimiter
                        fixed_content = content[:error_pos] + '"' + content[error_pos:]
                        try:
                            return json.loads(fixed_content)
                        except json.JSONDecodeError:
                            pass
        except Exception:
            pass
        
        # Strategy 2: Fix common JSON issues (trailing commas, single quotes, etc.)
        try:
            fixed = _repair_json(content)
            if fixed != content:
                result = json.loads(fixed)
                if logger:
                    logger.info("Successfully parsed JSON after repair (trailing commas / syntax fix)")
                return result
        except (json.JSONDecodeError, Exception):
            pass

        # Strategy 3: Dimension/trait JSON fallback - extract yes/no objects by key
        if logger:
            logger.warning("Trying dimension/trait fallback extraction...")
        try:
            dim_result = _extract_dimension_trait_json(content)
            if dim_result:
                if logger:
                    logger.info(f"Successfully extracted dimension/trait JSON via fallback. Keys: {list(dim_result.keys())}")
                return dim_result
        except Exception as e3:
            if logger:
                logger.debug(f"Dimension/trait fallback failed: {e3}")

        # Strategy 4: Summary/highlights/keywords regex fallback
        if logger:
            logger.warning("Trying summary fallback extraction method...")
        try:
            result = {}
            
            summary_pattern = r'"summary"\s*:\s*"((?:[^"\\]|\\.|\\n)*?)"(?:\s*[,}])'
            summary_match = re.search(summary_pattern, content, re.DOTALL)
            if not summary_match:
                summary_pattern = r'"summary"\s*:\s*"((?:[^"\\]|\\.|\\n)*?)(?:"\s*[,}]|(?=\s*"(?:highlights|keywords)"))'
                summary_match = re.search(summary_pattern, content, re.DOTALL)
            if not summary_match:
                summary_pattern = r'"summary"\s*:\s*"(.*?)"\s*,\s*"(?:highlights|keywords)"'
                summary_match = re.search(summary_pattern, content, re.DOTALL)
            if not summary_match:
                summary_pattern = r'"summary"\s*:\s*"((?:[^"\\]|\\.|\\n|.)*?)(?=\s*"(?:highlights|keywords)"|$)'
                summary_match = re.search(summary_pattern, content, re.DOTALL)
            if summary_match:
                summary_text = summary_match.group(1)
                summary_text = summary_text.replace('\\"', '"').replace('\\n', '\n').replace('\\\\', '\\')
                result["summary"] = summary_text
            
            highlights_pattern = r'"highlights"\s*:\s*\[(.*?)\]'
            highlights_match = re.search(highlights_pattern, content, re.DOTALL)
            if highlights_match:
                highlights_str = highlights_match.group(1)
                highlights = []
                for item_match in re.finditer(r'"((?:[^"\\]|\\.)*?)"', highlights_str):
                    item = item_match.group(1).replace('\\"', '"').replace('\\n', '\n')
                    highlights.append(item)
                result["highlights"] = highlights
            
            keywords_pattern = r'"keywords"\s*:\s*\[(.*?)\]'
            keywords_match = re.search(keywords_pattern, content, re.DOTALL)
            if keywords_match:
                keywords_str = keywords_match.group(1)
                keywords = []
                for item_match in re.finditer(r'"((?:[^"\\]|\\.)*?)"', keywords_str):
                    item = item_match.group(1).replace('\\"', '"').replace('\\n', '\n')
                    keywords.append(item)
                result["keywords"] = keywords
            
            if result:
                if logger:
                    logger.info(f"Successfully extracted JSON using summary fallback. Extracted keys: {list(result.keys())}")
                if "summary" not in result:
                    result["summary"] = ""
                if "highlights" not in result:
                    result["highlights"] = []
                if "keywords" not in result:
                    result["keywords"] = []
                return result
        except Exception as e2:
            if logger:
                logger.error(f"Summary fallback extraction also failed: {e2}")
        
        error_msg = f"Failed to parse JSON after multiple attempts. Original error: {e}"
        if hasattr(e, 'pos'):
            error_msg += f" at position {e.pos}"
        error_msg += f". Content preview (first 500 chars): {original_content[:500]}"
        if logger:
            logger.error(error_msg)
        raise


def _repair_json(text: str) -> str:
    """Attempt common JSON repairs: trailing commas, single quotes, unquoted keys."""
    fixed = text
    # Remove trailing commas before } or ]
    fixed = re.sub(r',\s*([}\]])', r'\1', fixed)
    # Replace single-quoted strings with double-quoted (only simple cases)
    # This handles cases like {'key': 'value'} but avoids breaking apostrophes in text
    if "'" in fixed and '"' not in fixed:
        fixed = fixed.replace("'", '"')
    return fixed


def _extract_dimension_trait_json(content: str) -> Optional[Dict[str, Any]]:
    """
    Extract dimension and trait yes/no values from malformed JSON.
    Handles the common LLM output pattern:
      { "dimensions": { "key": "yes", ... }, "traits": { "key": "no", ... } }
    """
    result = {}

    # Try to extract the dimensions block
    dim_match = re.search(r'"dimensions"\s*:\s*\{([^}]*(?:\{[^}]*\}[^}]*)*)\}', content, re.DOTALL)
    if dim_match:
        dim_block = dim_match.group(1)
        dims = {}
        for kv in re.finditer(r'"([^"]+)"\s*:\s*"(yes|no)"', dim_block, re.IGNORECASE):
            dims[kv.group(1)] = kv.group(2).lower()
        if dims:
            result["dimensions"] = dims

    # Try to extract the traits block
    trait_match = re.search(r'"traits"\s*:\s*\{([^}]*(?:\{[^}]*\}[^}]*)*)\}', content, re.DOTALL)
    if trait_match:
        trait_block = trait_match.group(1)
        traits = {}
        for kv in re.finditer(r'"([^"]+)"\s*:\s*"(yes|no)"', trait_block, re.IGNORECASE):
            traits[kv.group(1)] = kv.group(2).lower()
        if traits:
            result["traits"] = traits

    # If no nested structure found, try flat yes/no extraction
    if not result:
        flat = {}
        for kv in re.finditer(r'"([^"]+)"\s*:\s*"(yes|no)"', content, re.IGNORECASE):
            flat[kv.group(1)] = kv.group(2).lower()
        if flat:
            return flat

    return result if result else None
