"""Extra endpoint: Extract search filters from description using OpenAI."""
import json
from fastapi import APIRouter, HTTPException

from app.models.schemas import DescriptionRequest
from app.config import openai_client, logger

router = APIRouter()


@router.post("/api/v1/extract-filters")
async def extract_filters(payload: DescriptionRequest):
    """Extract search filters from description using OpenAI."""
    logger.info(f"=== EXTRACT FILTERS REQUEST START ===")
    logger.info(f"Request: description_length={len(payload.description) if payload.description else 0}")

    if not openai_client:
        logger.error("OpenAI client not initialized")
        raise HTTPException(status_code=500, detail="OpenAI client not initialized. Please check OPENAI_API_KEY.")

    logger.info("OpenAI client check passed")
    prompt = f"""
    Extract search filters from: "{payload.description}"

    Respond in JSON format only with these keys:
    - job_titles (list of strings)
    - skills (list of strings)
    - locations (list of countries/cities)
    - company_names (list of strings)
    - industries (list of strings)
    - company_sizes (list of strings like "1-10", "11-50", "10000+")
    - education_degrees (list of strings like "Bachelors", "Masters")
    - seniority_levels (list of strings)
    - job_roles (list of strings)
    - role_search_type ("Current Role Only" or "Entire History")
    - company_search_type ("Current Company Only" or "Entire History")
    """

    try:
        logger.info("Calling OpenAI API to extract filters")
        completion = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You extract structured job search parameters from text to JSON."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"}
        )
        result = json.loads(completion.choices[0].message.content)
        logger.info(f"Successfully extracted filters: {list(result.keys())}")
        logger.info(f"=== EXTRACT FILTERS REQUEST SUCCESS ===")
        return result
    except Exception as e:
        logger.error(f"=== ERROR in extract_filters ===")
        logger.error(f"OpenAI error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
