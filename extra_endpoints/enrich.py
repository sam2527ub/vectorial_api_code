"""Extra endpoint: Enrich job title using PDL API."""
from fastapi import APIRouter, HTTPException

from app.models.schemas import EnrichRequest
from app.config import pdl_client, logger

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
