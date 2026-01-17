"""Enrichment endpoints."""
import logging
from fastapi import APIRouter, HTTPException
from app.models.schemas import EnrichRequest, DescriptionRequest
from app.config import pdl_client, openai_client, logger
import json

router = APIRouter()


@router.post("/api/v1/enrich")
async def enrich_job_title(payload: EnrichRequest):
    """Enrich job title using PDL API."""
    logger.info(f"=== ENRICH JOB TITLE REQUEST START ===")
    logger.info(f"Request: job_title={payload.job_title}")
    
    try:
        if not pdl_client:
            logger.error("PDL client not initialized")
            raise HTTPException(status_code=503, detail="PDL client not initialized")
        
        logger.info(f"Enriching title: {payload.job_title}")
        response = pdl_client.job_title(job_title=payload.job_title).json()
        logger.info(f"PDL API response status: {response.get('status')}")
        
        if response.get("status") == 200:
            logger.info(f"=== ENRICH JOB TITLE REQUEST SUCCESS ===")
            return response.get('data')
        
        logger.error(f"PDL API error: {response.get('error', 'Unknown error')}")
        raise HTTPException(status_code=400, detail=response.get('error', 'PDL API Error'))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"=== ERROR in enrich_job_title ===")
        logger.error(f"Enrichment error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


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

