"""JSON parsing and cleaning utilities."""
import json
from typing import Dict, Any


def clean_json_content(content: str) -> str:
    """Clean JSON content by removing markdown code blocks and fixing formatting."""
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
    """Parse JSON response from string, with cleaning."""
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
