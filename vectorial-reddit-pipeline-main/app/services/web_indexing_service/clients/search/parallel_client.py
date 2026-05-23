# app/services/web_indexing_service/clients/search/parallel_client.py
import httpx
from typing import Any, Dict, List, Optional
from datetime import datetime
from app.config import logger
from app.services.web_indexing_service.config import WebIndexingConfig
from .interface import SearchClientInterface
from .parallel_utils import (
    extract_findall_id,
    extract_candidates,
    process_candidates,
    log_metrics_warning
)

class ParallelSearchClient(SearchClientInterface):
    """Parallel API implementation of SearchClientInterface."""
    
    def __init__(self, config: Optional[WebIndexingConfig] = None):
        self.config = config or WebIndexingConfig()
        self._check_availability()
    
    def _check_availability(self) -> None:
        """Validate configuration. Raises on failure."""
        self.config.validate_dependencies()
        logger.info("Parallel search client initialized successfully for web indexing")
    
    def _get_http_client(self) -> httpx.AsyncClient:
        """Get configured HTTP client with timeout."""
        return httpx.AsyncClient(timeout=self.config.parallel_api_timeout)
    
    def is_configured(self) -> bool:
        return bool(self.config.parallel_api_key)
    
    def get_raw_client(self) -> Any:
        return self
    
    async def start_scraping_job(
        self,
        query: str,
        model: str,
        match_limit: int,
        entity_type: str = "subreddit"
    ) -> Dict[str, Any]:
        """Start a parallel scraping job and return findall_id."""
        payload = {
            "objective": query,
            "entity_type": entity_type,
            "match_conditions": [{"name": "query_match", "description": query}],
            "generator": model,
            "match_limit": match_limit
        }
        
        logger.info(f"Starting Parallel scraping job: query={query}, model={model}, limit={match_limit}")
        
        headers = self.config.get_api_headers()
        async with self._get_http_client() as client:
            response = await client.post(
                f"{self.config.parallel_api_base_url}/runs",
                json=payload,
                headers=headers
            )
            
            if response.status_code not in [200, 201]:
                error_text = response.text[:500] if hasattr(response, 'text') else str(response)
                logger.error(f"Parallel API error: {response.status_code} - {error_text}")
                response.raise_for_status()
            
            run_data = response.json()
            findall_id = extract_findall_id(run_data)
            
            logger.info(f"Parallel scraping job started: findall_id={findall_id}")
            return {"findall_id": findall_id, "data": run_data}
    
    async def get_job_status(self, job_id: str) -> Dict[str, Any]:
        """Get status of a parallel scraping job."""
        findall_id = job_id  # Map generic job_id to Parallel-specific findall_id
        headers = self.config.get_api_headers()
        
        async with self._get_http_client() as client:
            response = await client.get(
                f"{self.config.parallel_api_base_url}/runs/{findall_id}",
                headers=headers
            )
            
            if response.status_code == 404:
                logger.warning(f"Parallel findall run {findall_id} not found (404)")
                return {"status": "not_found", "findall_id": findall_id}
            
            response.raise_for_status()
            return response.json()
    
    async def fetch_job_results(self, job_id: str) -> List[Dict[str, Any]]:
        """Fetch results from a completed Parallel findall run.
        Uses the official result endpoint: /runs/{findall_id}/result
        """
        findall_id = job_id  # Map generic job_id to Parallel-specific findall_id
        logger.info(f"Fetching results for findall_id={findall_id}")
        start_time = datetime.now()
        
        try:
            async with self._get_http_client() as client:
                headers = self.config.get_api_headers()
                endpoint = f"/runs/{findall_id}/result"
                
                # Use official result endpoint per Parallel FindAll API docs
                response = await client.get(
                    f"{self.config.parallel_api_base_url}{endpoint}",
                    headers=headers
                )
                
                if response.status_code != 200:
                    logger.warning(f"Result endpoint returned status {response.status_code} for findall_id={findall_id}")
                    return []
                
                data = response.json()
                candidates = extract_candidates(data)
                
                if not candidates:
                    log_metrics_warning(data, f"{endpoint}: ")
                    logger.warning(f"No candidates found in result response")
                    return []
                
                # Process candidates and filter to matched only
                subreddits = process_candidates(candidates, require_match=True)
                
                if not subreddits:
                    logger.warning(f"No matched candidates found. Total candidates: {len(candidates)}")
                    log_metrics_warning(data)
                    return []
                
                elapsed = (datetime.now() - start_time).total_seconds()
                logger.info(f"Fetched {len(subreddits)} subreddits in {elapsed:.2f}s")
                return subreddits
                    
        except Exception as e:
            logger.error(f"Error fetching results for findall_id={findall_id}: {e}", exc_info=True)
            raise
