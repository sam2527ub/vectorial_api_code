"""Status handler for web indexing runs."""
import httpx
from typing import Dict, Any, Optional
from app.config import logger
from app.services.web_indexing_service.clients.search.factory import get_search_client
from app.services.web_indexing_service.config import WebIndexingConfig


class WebIndexingStatusHandler:
    """Handles status checking and result fetching for web indexing runs."""
    
    def __init__(self, config: Optional[WebIndexingConfig] = None):
        """Initialize status handler."""
        self.config = config or WebIndexingConfig()
        self.api_client = get_search_client()
    
    def _extract_status(self, api_response: Dict[str, Any]) -> str:
        """Extract status from API response."""
        status_obj = api_response.get("status", {})
        if isinstance(status_obj, dict):
            return status_obj.get("status", "").upper()
        return str(status_obj).upper() if status_obj else ""
    
    async def check_job_status(
        self,
        job_id: str,
        enterprise_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """Check run status directly from API. Returns API response (with results added if completed)."""
        try:
            api_response = await self.api_client.get_job_status(job_id)
            
            # Handle not found
            if api_response.get("status") == "not_found":
                logger.warning(f"Job run {job_id} not found")
                return api_response
            
            # Extract status to check if completed
            run_status = self._extract_status(api_response)
            
            # If completed, fetch and add results to the API response
            if run_status in ("COMPLETED", "SUCCEEDED"):
                logger.info(f"Job run completed for job_id {job_id}, fetching results")
                results = await self.api_client.fetch_job_results(job_id)
                # Add results to existing API response structure
                api_response["subreddits"] = results
                api_response["subreddits_collected"] = len(results)
            
            # Return API response as-is (with results added if completed)
            return api_response
        
        except httpx.TimeoutException:
            logger.error(f"Timeout checking status for job_id {job_id}")
            return {
                "status": "processing",
                "message": "Timeout checking provider API status"
            }
        
        except Exception as e:
            logger.error(f"Error checking status for job_id {job_id}: {e}", exc_info=True)
            return {
                "status": "processing",
                "message": f"Error checking provider API status: {str(e)}"
            }
