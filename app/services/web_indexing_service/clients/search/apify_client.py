"""Apify client for web indexing (LinkedIn Company Employees Scraper)."""
import httpx
from typing import Any, Dict, List, Optional

from app.config import logger
from app.services.web_indexing_service.config import WebIndexingConfig
from .interface import SearchClientInterface
from .apify_utils import apify_dataset_to_profiles


class ApifySearchClient(SearchClientInterface):
    """Apify actor client for LinkedIn Company Employees (filter-based search)."""

    def __init__(self, config: Optional[WebIndexingConfig] = None):
        self.config = config or WebIndexingConfig()
        if self.is_configured():
            logger.debug("Apify search client initialized for web indexing (LinkedIn Company Employees)")

    def _actor_id_for_url(self) -> str:
        """Apify API expects owner~actor-name; we store owner/actor-name."""
        return (self.config.apify_actor_id or "").replace("/", "~")

    def _get_http_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=60.0)

    def is_configured(self) -> bool:
        return bool(self.config.apify_token)

    def get_raw_client(self) -> Any:
        return self

    async def start_scraping_job(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Start an Apify actor run. params: companies (required), jobTitles, locations,
        profileScraperMode, maxItems, companyBatchMode, startPage, etc.
        Returns dict with run id as job_id.
        """
        actor_id = self._actor_id_for_url()
        if not actor_id:
            raise ValueError("APIFY_ACTOR_ID not configured")
        url = f"{self.config.apify_api_base_url}/acts/{actor_id}/runs"
        headers = self.config.get_apify_headers()
        # Build actor input from params; pass through known keys
        input_body = {
            k: v for k, v in params.items()
            if v is not None and k not in ("entity_type", "query", "model", "match_limit")
        }
        if "companies" not in input_body:
            raise ValueError("Apify input must include 'companies' (list of company URLs or names)")
        timeout_sec = self.config.apify_run_timeout_seconds
        logger.info(f"Starting Apify run: actor={actor_id}, timeout={timeout_sec}s, companies={len(input_body.get('companies', []))}")
        async with self._get_http_client() as client:
            response = await client.post(
                url,
                json=input_body,
                headers=headers,
                params={"timeout": timeout_sec} if timeout_sec else None,
            )
            if response.status_code not in (200, 201):
                error_text = response.text[:500] if response.text else str(response.status_code)
                logger.error(f"Apify API error: {response.status_code} - {error_text}")
                response.raise_for_status()
            data = response.json()
        run_data = data.get("data", data)
        run_id = run_data.get("id")
        if not run_id:
            logger.error(f"No run id in Apify response. Keys: {list(run_data.keys())}")
            raise ValueError("Apify API did not return run id")
        logger.info(f"Apify run started: run_id={run_id}")
        return {"findall_id": run_id, "data": run_data}

    async def get_job_status(self, job_id: str) -> Dict[str, Any]:
        """Get run status from Apify. job_id is the Apify run id."""
        url = f"{self.config.apify_api_base_url}/actor-runs/{job_id}"
        headers = self.config.get_apify_headers()
        async with self._get_http_client() as client:
            response = await client.get(url, headers=headers)
            if response.status_code == 404:
                logger.warning(f"Apify run {job_id} not found (404)")
                return {"status": "not_found", "findall_id": job_id}
            response.raise_for_status()
            return response.json()

    async def fetch_job_results(self, job_id: str) -> List[Dict[str, Any]]:
        """Fetch dataset items for completed run and normalize to common profile shape."""
        logger.info(f"Fetching Apify run dataset for job_id={job_id}")
        url = f"{self.config.apify_api_base_url}/actor-runs/{job_id}/dataset/items"
        headers = self.config.get_apify_headers()
        async with self._get_http_client() as client:
            response = await client.get(url, headers=headers)
            if response.status_code != 200:
                logger.warning(f"Apify dataset items returned {response.status_code} for run {job_id}")
                return []
            items = response.json()
        if not isinstance(items, list):
            items = []
        profiles = apify_dataset_to_profiles(items)
        logger.info(f"Fetched {len(profiles)} profiles from Apify run {job_id}")
        return profiles
