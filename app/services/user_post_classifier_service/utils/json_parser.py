"""Parse classifier JSON from Groq response content."""
import json
from typing import Any, Dict, List


def parse_classifier_response(content: str) -> List[Dict[str, Any]]:
    """
    Extract classifications array from Groq response (may contain markdown/code blocks).
    Raises json.JSONDecodeError or ValueError if parsing fails.
    """
    if not content or not content.strip():
        raise ValueError("Empty response content")

    raw = content.strip()
    if "```json" in raw:
        start = raw.find("```json") + 7
        end = raw.find("```", start)
        if end != -1:
            raw = raw[start:end].strip()
    elif "```" in raw:
        start = raw.find("```") + 3
        end = raw.find("```", start)
        if end != -1:
            raw = raw[start:end].strip()

    if not raw.startswith("{"):
        start_idx = raw.find("{")
        if start_idx == -1:
            raise json.JSONDecodeError("No JSON object found", content, 0)
        raw = raw[start_idx:]

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        brace_count = 0
        end_idx = -1
        for i, char in enumerate(raw):
            if char == "{":
                brace_count += 1
            elif char == "}":
                brace_count -= 1
                if brace_count == 0:
                    end_idx = i + 1
                    break
        if end_idx > 0:
            result = json.loads(raw[:end_idx])
        else:
            raise

    classifications = result.get("classifications", [])
    if not isinstance(classifications, list):
        raise ValueError(f"Expected classifications array, got {type(classifications)}")
    return classifications
