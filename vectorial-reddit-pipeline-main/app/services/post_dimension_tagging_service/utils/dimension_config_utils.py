"""Utilities for fetching and building dimension configs."""
import os
import re
import json
from typing import Dict, Any
from app.config import logger
from PromptService import PromptService

langsmith_api_key = os.getenv("LANGSMITH_API_KEY")

_config_service = PromptService(
    api_key=langsmith_api_key,
    cache_duration=600,
)


def _clean_json_string(raw: str) -> str:
    """Remove markdown fences, trailing commas, and other common issues."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    # Remove trailing commas before } or ]
    text = re.sub(r',\s*([}\]])', r'\1', text)
    # Extract outermost JSON object if surrounded by extra text
    match = re.search(r'\{', text)
    if match and match.start() > 0:
        text = text[match.start():]
    return text


def get_dimension_config(category: str) -> Dict[str, Any]:
    """
    Fetch and parse dimension config from LangSmith.
    Applies JSON repair (trailing comma removal, fence stripping) before parsing.
    """
    config_name = f"dimension_tagging_config_{category}"
    config_str = None
    try:
        config_prompt = _config_service.get_prompt(config_name)

        if hasattr(config_prompt, "content") and config_prompt.content:
            config_str = config_prompt.content
        elif hasattr(config_prompt, "template") and config_prompt.template:
            config_str = config_prompt.template
        elif hasattr(config_prompt, "messages") and config_prompt.messages:
            first_msg = config_prompt.messages[0]
            if isinstance(first_msg, dict):
                config_str = first_msg.get("content", "")
            else:
                config_str = getattr(first_msg, "content", None)
        
        if not config_str or not config_str.strip():
            raise ValueError(f"Config prompt '{config_name}' returned empty content")

        cleaned = _clean_json_string(config_str)

        try:
            config_dict = json.loads(cleaned)
        except json.JSONDecodeError:
            # Second attempt: strip to first { and last }
            start = cleaned.find('{')
            end = cleaned.rfind('}')
            if start != -1 and end != -1 and end > start:
                config_dict = json.loads(cleaned[start:end + 1])
            else:
                raise

        category_upper = category.upper()
        return config_dict.get(category_upper, config_dict)
    
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JSON from config '{config_name}': {e}")
        logger.error(f"Config content (first 500 chars): {config_str[:500] if config_str else 'N/A'}")
        raise
    except Exception as e:
        logger.error(f"Failed to fetch dimension config '{config_name}': {e}")
        raise


def build_tagging_questions_list(dimension_config: Dict[str, Any]) -> str:
    """
    Build bullet list of tagging questions from dimension config.
    
    Args:
        dimension_config: Dict of dimension_key -> dimension_config
        
    Returns:
        Formatted string with bullet list of questions
    """
    questions = []
    for dim_key, dim_config in dimension_config.items():
        question = dim_config.get("tagging_question", "")
        if question:
            questions.append(f"- {dim_key}: {question}")
    return "\n".join(questions)


def build_dynamic_tagging_schema(dimension_config: Dict[str, Any]) -> str:
    """
    Build JSON schema example from dimension config.
    
    Args:
        dimension_config: Dict of dimension_key -> dimension_config
        
    Returns:
        Formatted JSON schema string
    """
    schema_dict = {dim_key: "yes/no" for dim_key in dimension_config.keys()}
    schema_str = json.dumps(schema_dict, indent=2)
    return schema_str
