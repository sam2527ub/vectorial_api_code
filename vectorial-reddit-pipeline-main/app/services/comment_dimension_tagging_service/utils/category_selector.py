"""Utility for selecting category and dimensions for comment dimension tagging."""
import json
from typing import Tuple, Any, List, Dict
from app.config import logger
from app.utils.s3_utils import extract_s3_key_from_url
from ..prompts import comment_tait_dimension_tagging_prompt
from ..clients.storage.factory import get_storage_client
from .dimension_config_utils import (
    get_dimension_config,
    build_tagging_questions_list,
    build_dynamic_tagging_schema
)
from PromptService import FallbackPrompt


def _fetch_description_data(audience_room: Any) -> Dict[str, Any]:
    """Fetch audience room description JSON from S3."""
    description_url = getattr(audience_room, "descriptionS3Url", None)
    if not description_url:
        return {}

    description_key = extract_s3_key_from_url(description_url)
    if not description_key:
        logger.warning(f"Audience room {audience_room.id}: invalid description S3 URL")
        return {}

    storage_client = get_storage_client()
    if not storage_client or not storage_client.is_configured():
        logger.warning("Storage client unavailable while loading audience traits")
        return {}

    try:
        description_data = storage_client.read_json(description_key)
        return description_data if isinstance(description_data, dict) else {}
    except Exception as e:
        logger.warning(f"Audience room {audience_room.id}: failed to load description JSON: {e}")
        return {}


def _extract_trait_titles(description_data: Dict[str, Any]) -> List[str]:
    """Extract trait titles from audience room description JSON."""
    try:
        traits = description_data.get("traits", [])
        titles = [
            t.get("title", "").strip()
            for t in traits
            if isinstance(t, dict) and t.get("title")
        ]
        return list(dict.fromkeys([t for t in titles if t]))
    except Exception as e:
        logger.warning(f"Failed to extract trait titles from description JSON: {e}")
        return []


def _extract_room_summary(description_data: Dict[str, Any]) -> str:
    """Extract room summary text from audience room description JSON."""
    summary = description_data.get("summary", "")
    if isinstance(summary, str):
        return summary.strip()
    return ""


def _build_trait_tagging_questions_list(trait_titles: List[str]) -> str:
    """Build bullet list of trait checks."""
    return "\n".join([f"- {title}: Is there explicit evidence of this trait in this content?" for title in trait_titles])


def _build_trait_schema(trait_titles: List[str]) -> str:
    """Build trait schema as JSON object string."""
    return json.dumps({title: "yes/no" for title in trait_titles}, indent=2)


def get_category_and_dimensions(audience_room: Any) -> Tuple[str, list, list, Any]:
    """
    Determine category and get corresponding dimensions and prompt template.
    
    Args:
        audience_room: Audience room object with category attribute
        
    Returns:
        Tuple of (category_lower, dimensions, trait_titles, prompt_template)
        
    Raises:
        ValueError: If category is invalid
    """
    category = getattr(audience_room, 'category', None)
    if not category:
        category_lower = "b2b"
        logger.debug(
            f"Audience room {audience_room.id}: category not set, using default b2b"
        )
    else:
        category_lower = category.lower().strip()
    
    if category_lower not in ["b2b", "b2c"]:
        raise ValueError(f"Invalid category: {category}. Must be 'b2b' or 'b2c'")
    
    # Fetch dimension config
    try:
        dimension_config = get_dimension_config(category_lower)
        
        # Extract dimension keys (these will be the dimension names)
        dimensions = list(dimension_config.keys())
        
        # Build tagging questions list
        tagging_questions = build_tagging_questions_list(dimension_config)
        
        # Build dynamic tagging schema
        dynamic_schema = build_dynamic_tagging_schema(dimension_config)
        
        description_data = _fetch_description_data(audience_room)
        trait_titles = _extract_trait_titles(description_data)
        room_summary = _extract_room_summary(description_data)
        trait_questions = _build_trait_tagging_questions_list(trait_titles)
        trait_schema = _build_trait_schema(trait_titles)

        # Get the raw prompt object and extract the template string
        prompt_obj = comment_tait_dimension_tagging_prompt.prompt_service.get_prompt(
            comment_tait_dimension_tagging_prompt.prompt_name
        )

        # Extract template from LangChain prompt structure
        if hasattr(prompt_obj, 'messages') and prompt_obj.messages:
            # LangChain ChatPromptTemplate - extract from first message
            raw_prompt = prompt_obj.messages[0].prompt.template
        elif hasattr(prompt_obj, 'template'):
            raw_prompt = str(prompt_obj.template)
        elif isinstance(prompt_obj, FallbackPrompt):
            raw_prompt = prompt_obj.template
        else:
            raw_prompt = str(prompt_obj)

        # Manually replace placeholders to avoid double-escaping issues
        # This preserves {context_summary} and {comment_body} for later formatting
        formatted_prompt = raw_prompt.replace("{TAGGING_QUESTIONS_LIST}", tagging_questions)
        formatted_prompt = formatted_prompt.replace("{DYNAMIC_TAGGING_SCHEMA}", dynamic_schema)
        formatted_prompt = formatted_prompt.replace(
            "{TRAIT_TAGGING_QUESTIONS_LIST}",
            trait_questions if trait_questions else "- No audience traits available."
        )
        formatted_prompt = formatted_prompt.replace("{DYNAMIC_TRAITS_SCHEMA}", trait_schema)
        formatted_prompt = formatted_prompt.replace(
            "{AUDIENCE_ROOM_SUMMARY}",
            room_summary if room_summary else "No audience room summary available."
        )

        # Create a FallbackPrompt wrapper that can be formatted again with context_summary/comment_body

        logger.info(f"Formatted prompt: {formatted_prompt}")
        prompt_template = FallbackPrompt(formatted_prompt)
        
        logger.debug(
            f"Loaded {len(dimensions)} dimensions from config for category {category_lower}"
        )
        
    except Exception as e:
        logger.error(f"Failed to load dimension config for {category_lower}: {e}")
        raise
    
    return category_lower, dimensions, trait_titles, prompt_template
