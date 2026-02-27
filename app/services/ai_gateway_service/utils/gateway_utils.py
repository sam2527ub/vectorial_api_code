"""
AI Gateway Utilities - Message formatting, model normalization, JSON parsing.

- Model name normalization (adds provider prefixes for Vercel gateway)
- Message formatting for Claude API (extracts system messages)
- Model provider detection (openai, anthropic, groq)
- Robust parse_json_response for AI responses (markdown, malformed JSON)
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


def _clean_json_content(content: str) -> str:
    """Remove markdown code blocks and fix leading/trailing formatting."""
    content = content.strip()
    if not content:
        return content
    if "```json" in content:
        start = content.find("```json") + 7
        end = content.find("```", start)
        if end != -1:
            content = content[start:end].strip()
    elif "```" in content:
        start = content.find("```") + 3
        end = content.find("```", start)
        if end != -1:
            content = content[start:end].strip()
    if not content.startswith("{"):
        s = content.strip()
        if s.startswith('"') and not s.startswith('{"'):
            content = "{" + s + ("}" if not s.endswith("}") else "")
    return content


def _fix_malformed_json(content: str) -> str:
    """Best-effort fix truncated/unterminated JSON."""
    if not content or not content.strip():
        return content
    brace_count = 0
    last_valid = -1
    for i, c in enumerate(content):
        if c == "{":
            brace_count += 1
        elif c == "}":
            brace_count -= 1
            if brace_count == 0:
                last_valid = i
        elif brace_count < 0:
            break
    if last_valid > 0:
        return content[: last_valid + 1]
    return content


def parse_json_response(content: str, logger=None) -> Dict[str, Any]:
    """
    Parse JSON from AI response. Handles markdown, malformed/truncated JSON.
    Raises ValueError if parsing fails after all attempts.
    """
    cleaned = _clean_json_content(content)
    if not cleaned.startswith("{"):
        raise ValueError(f"Response is not valid JSON. Got: {cleaned[:100]}")
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    fixed = _fix_malformed_json(cleaned)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass
    brace_count = 0
    last_valid = -1
    for i, c in enumerate(cleaned):
        if c == "{":
            brace_count += 1
        elif c == "}":
            brace_count -= 1
            if brace_count == 0:
                last_valid = i
        elif brace_count < 0:
            break
    if last_valid > 0:
        try:
            return json.loads(cleaned[: last_valid + 1])
        except json.JSONDecodeError:
            pass
    # Regex fallback for summary/highlights/keywords
    result = {}
    try:
        summary_m = re.search(r'"summary"\s*:\s*"((?:[^"\\]|\\.)*)"', cleaned, re.DOTALL)
        if not summary_m:
            summary_m = re.search(r'"summary"\s*:\s*"([^"]*)', cleaned, re.DOTALL)
        if summary_m:
            result["summary"] = (
                summary_m.group(1)
                .replace("\\n", "\n")
                .replace('\\"', '"')
                .replace("\\\\", "\\")
            )
        for key, pattern in [
            ("highlights", r'"highlights"\s*:\s*\[(.*?)\]'),
            ("keywords", r'"keywords"\s*:\s*\[(.*?)\]'),
        ]:
            m = re.search(pattern, cleaned, re.DOTALL)
            if m:
                items = re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(1))
                result[key] = [it.replace('\\"', '"').replace("\\\\", "\\") for it in items]
        if result:
            if "summary" not in result:
                result["summary"] = ""
            if "highlights" not in result:
                result["highlights"] = []
            if "keywords" not in result:
                result["keywords"] = []
            if logger:
                logger.warning(f"Extracted partial JSON: {list(result.keys())}")
            return result
    except Exception:
        pass
    raise ValueError(f"Invalid JSON after multiple attempts. Preview: {cleaned[:200]}...")
