"""JSON parsing utilities for AI responses."""
import json
import re
from typing import Dict, Any
from app.config import logger


def extract_and_parse_json(
    response_text: str,
    max_log_length: int = 2000
) -> Dict[str, Any]:
    """Extract JSON from model response and parse it, handling common issues."""
    # Log raw response for debugging (truncated)
    if len(response_text) > max_log_length:
        logger.info(
            f"Raw traits response (first {max_log_length} chars): "
            f"{response_text[:max_log_length]}"
        )
        logger.info(f"Raw traits response (last 500 chars): {response_text[-500:]}")
    else:
        logger.info(f"Raw traits response: {response_text}")
    
    # Try to extract JSON from code blocks first
    json_text = response_text.strip()
    
    # Remove markdown code blocks if present
    if "```json" in json_text:
        # Extract content between ```json and ``` (non-greedy, but also handle incomplete closing)
        match = re.search(r'```json\s*(.*?)(?:\s*```|$)', json_text, re.DOTALL)
        if match:
            json_text = match.group(1).strip()
            # If no closing ``` found, the JSON might be incomplete - log warning
            if "```" not in response_text[response_text.find("```json") + 7:]:
                logger.warning("JSON response appears to be incomplete (no closing ``` found)")
    elif "```" in json_text:
        # Extract content between ``` and ``` (non-greedy, but also handle incomplete closing)
        match = re.search(r'```\s*(.*?)(?:\s*```|$)', json_text, re.DOTALL)
        if match:
            json_text = match.group(1).strip()
            # If no closing ``` found, the JSON might be incomplete - log warning
            if json_text.count("```") < 2:
                logger.warning("JSON response appears to be incomplete (no closing ``` found)")
    
    # Try to find JSON object boundaries if not in code block
    if not json_text.startswith('{'):
        # Look for first { and last }
        first_brace = json_text.find('{')
        last_brace = json_text.rfind('}')
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            json_text = json_text[first_brace:last_brace + 1]
    
    # Try to parse the JSON
    try:
        return json.loads(json_text)
    except json.JSONDecodeError as e:
        # Log the problematic JSON for debugging
        error_pos = getattr(e, 'pos', None)
        if error_pos:
            start = max(0, error_pos - 200)
            end = min(len(json_text), error_pos + 200)
            logger.error(
                f"JSON parse error at position {error_pos}: {json_text[start:end]}"
            )
        else:
            logger.error(f"JSON parse error: {str(e)}")
        
        # Log full response if it's not too long
        if len(json_text) <= max_log_length:
            logger.error(f"Full JSON text that failed to parse: {json_text}")
        else:
            logger.error(
                f"JSON text (truncated, first {max_log_length} chars): "
                f"{json_text[:max_log_length]}"
            )
            logger.error(f"JSON text (last 500 chars): {json_text[-500:]}")
        
        # Try to use a more lenient approach - look for the JSON object more carefully
        # Sometimes the model includes extra text before/after the JSON
        try:
            # Try to find a complete JSON object by matching braces
            brace_count = 0
            start_idx = -1
            for i, char in enumerate(json_text):
                if char == '{':
                    if start_idx == -1:
                        start_idx = i
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0 and start_idx != -1:
                        # Found complete JSON object
                        extracted_json = json_text[start_idx:i+1]
                        logger.info(
                            f"Extracted JSON object from position {start_idx} to {i+1}"
                        )
                        return json.loads(extracted_json)
            
            # If we got here, braces don't match - JSON might be incomplete
            if start_idx != -1 and brace_count > 0:
                logger.warning(
                    f"JSON appears incomplete: {brace_count} unclosed braces. "
                    f"This might be due to max_tokens limit being too low."
                )
                # Try to close the JSON manually by adding closing braces
                # This is a last resort - ideally we should increase max_tokens
                incomplete_json = json_text[start_idx:]
                # Add closing braces for unclosed ones
                for _ in range(brace_count):
                    incomplete_json += "}"
                try:
                    logger.warning("Attempting to parse incomplete JSON by adding closing braces")
                    return json.loads(incomplete_json)
                except json.JSONDecodeError:
                    pass
        except (json.JSONDecodeError, ValueError) as e2:
            logger.error(f"Failed to extract JSON using brace matching: {e2}")
        
        raise ValueError(
            f"Failed to parse JSON: {str(e)}. "
            f"This might indicate the response was truncated. "
            f"Consider increasing max_tokens for traits generation."
        )
