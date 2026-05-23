"""Request handler for web indexing service."""
from typing import Dict, Any, Optional
from fastapi import HTTPException
from app.config import logger
from app.services.web_indexing_service.config import WebIndexingConfig
from app.services.web_indexing_service.clients.search.factory import get_search_client
from app.services.web_indexing_service.status.web_indexing_status_handler import WebIndexingStatusHandler


class WebIndexingHandler:
    """Handles web indexing requests."""
    
    def __init__(self):
        """Initialize request handler."""
        self.config = WebIndexingConfig()
        self.api_client = get_search_client()
        self.status_handler = WebIndexingStatusHandler(self.config)
    
    def _validate_config(self) -> None:
        """Validate that indexing service is configured."""
        if not self.api_client.is_configured():
            logger.error("Indexing service API key not configured in environment")
            raise HTTPException(
                status_code=503, 
                detail="Indexing service not configured"
            )
    
    async def start_scraping_job(
        self,
        query: str,
        model: str,
        match_limit: int,
        entity_type: str,
        enterprise_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """Start a new web indexing run. Returns API response directly."""
        try:
            self._validate_config()
            api_response = await self.api_client.start_scraping_job(
                query=query,
                model=model,
                match_limit=match_limit,
                entity_type=entity_type
            )
            # Normalize so response always has job_id and status for the API contract
            data = api_response.get("data", api_response)
            job_id = api_response.get("findall_id") or data.get("job_id") or data.get("findall_id")
            return {"job_id": job_id, "status": "started", **data}
        
        except HTTPException:
            raise
        except ValueError as e:
            logger.error(f"Validation error: {e}")
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(f"Error starting job: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Error starting search: {str(e)}")
    
    async def get_job_status(
        self,
        job_id: str,
        enterprise_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get status of a scraping run. Returns API response directly."""
        try:
            return await self.status_handler.check_job_status(job_id, enterprise_name)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting job status: {e}", exc_info=True)
            # Return simple error response matching API structure
            return {
                "status": "processing",
                "message": f"Error checking status, will retry: {str(e)}"
            }
