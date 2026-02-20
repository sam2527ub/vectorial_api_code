"""Parallel API client for web indexing (people/LinkedIn parallel search)."""
import json
import httpx
from typing import Any, Dict, List, Optional
from app.config import logger
from app.services.web_indexing_service.config import WebIndexingConfig
from .interface import SearchClientInterface
from .parallel_utils import (
    extract_findall_id,
    extract_candidates,
    process_candidates_to_profiles,
    extract_run_status,
)


class ParallelSearchClient(SearchClientInterface):
    """Parallel FindAll API client for people/LinkedIn search."""

    def __init__(self, config: Optional[WebIndexingConfig] = None):
        self.config = config or WebIndexingConfig()
        if self.is_configured():
            logger.debug("Parallel search client initialized for web indexing (people)")

    def _get_http_client(self) -> httpx.AsyncClient:
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
        entity_type: str = "people",
    ) -> Dict[str, Any]:
        """Start a parallel search run (people). Returns findall_id and full response."""
        run_payload = {
            "objective": query,
            "entity_type": entity_type,
            "match_conditions": [
                {"name": "query_match", "description": query}
            ],
            "generator": model,
            "match_limit": match_limit,
        }
        logger.info(f"Starting Parallel run: query={query}, model={model}, limit={match_limit}")
        headers = self.config.get_api_headers()
        async with self._get_http_client() as client:
            response = await client.post(
                f"{self.config.parallel_api_base_url}/runs",
                json=run_payload,
                headers=headers,
            )
            if response.status_code not in (200, 201):
                error_text = response.text[:500] if hasattr(response, "text") else str(response)
                logger.error(f"Parallel API error: {response.status_code} - {error_text}")
                response.raise_for_status()
            run_data = response.json()
            findall_id = extract_findall_id(run_data)
            logger.info(f"Parallel run started: findall_id={findall_id}")
            return {"findall_id": findall_id, "data": run_data}

    async def get_job_status(self, job_id: str) -> Dict[str, Any]:
        """Get run status from Parallel API. job_id is findall_id (parallel run id)."""
        headers = self.config.get_api_headers()
        async with self._get_http_client() as client:
            response = await client.get(
                f"{self.config.parallel_api_base_url}/runs/{job_id}",
                headers=headers,
            )
            if response.status_code == 404:
                logger.warning(f"Parallel run {job_id} not found (404)")
                return {"status": "not_found", "findall_id": job_id}
            response.raise_for_status()
            return response.json()

    async def fetch_job_results(self, job_id: str) -> List[Dict[str, Any]]:
        """Fetch result from completed run; return list of profile dicts (LinkedIn). Tries /result then SSE fallback."""
        logger.info(f"Fetching results for run {job_id}")
        headers = self.config.get_api_headers()
        async with self._get_http_client() as client:
            response = await client.get(
                f"{self.config.parallel_api_base_url}/runs/{job_id}/result",
                headers=headers,
            )
            if response.status_code == 200:
                data = response.json()
                candidates = extract_candidates(data)
                if candidates:
                    profiles = process_candidates_to_profiles(candidates)
                    logger.info(f"Fetched {len(profiles)} profiles from result endpoint for run {job_id}")
                    return profiles
            logger.warning(f"Result endpoint returned {response.status_code} or empty; trying SSE fallback for run {job_id}")
        profiles = await self._fetch_profiles_from_sse(job_id)
        if profiles:
            logger.info(f"Fetched {len(profiles)} profiles from SSE fallback for run {job_id}")
        return profiles

    async def _fetch_profiles_from_sse(self, parallel_run_id: str) -> List[Dict[str, Any]]:
        """Fallback: fetch profiles from SSE events stream (no Apify)."""
        profiles = []
        try:
            sse_headers = {
                **self.config.get_api_headers(),
                "Accept": "text/event-stream",
            }
            sse_url = f"{self.config.parallel_api_base_url}/runs/{parallel_run_id}/events"
            logger.info(f"Fetching results from SSE stream: {sse_url}")
            async with httpx.AsyncClient(timeout=60.0) as client:
                async with client.stream("GET", sse_url, headers=sse_headers, timeout=60.0) as sse_response:
                    if sse_response.status_code != 200:
                        logger.error(f"Failed to connect to SSE stream: {sse_response.status_code}")
                        return profiles
                    buffer = ""
                    async for chunk in sse_response.aiter_bytes():
                        if not chunk:
                            continue
                        try:
                            buffer += chunk.decode("utf-8", errors="replace")
                            while "\n" in buffer:
                                line, buffer = buffer.split("\n", 1)
                                line = line.strip()
                                if not line or line.startswith("event:") or line.startswith("id:") or line.startswith("retry:"):
                                    continue
                                event_json = line[6:] if line.startswith("data: ") else line
                                if not event_json:
                                    continue
                                try:
                                    event_data = json.loads(event_json)
                                    event_type = (event_data.get("type") or event_data.get("event") or "").lower()
                                    if "candidate" in event_type and "matched" in event_type:
                                        candidate = event_data.get("data", {})
                                        if "candidate" in candidate:
                                            candidate = candidate.get("candidate", {})
                                        linkedin_url = (
                                            candidate.get("linkedin_url")
                                            or candidate.get("url")
                                            or candidate.get("profile_url")
                                            or ""
                                        )
                                        if linkedin_url and "linkedin.com" in linkedin_url:
                                            profile = {
                                                "linkedin_url": linkedin_url,
                                                "name": candidate.get("name", ""),
                                                "description": candidate.get("description", ""),
                                                "match_status": "matched",
                                                "output": candidate.get("output", {}),
                                                "basis": candidate.get("basis", []),
                                            }
                                            profiles.append(profile)
                                            logger.debug(f"Collected LinkedIn URL from SSE: {linkedin_url}")
                                    elif "completed" in event_type or "run_completed" in event_type:
                                        logger.info("Parallel run completed (from SSE)")
                                        break
                                except json.JSONDecodeError:
                                    continue
                            if "completed" in buffer.lower() or "run_completed" in buffer.lower():
                                break
                        except Exception as chunk_error:
                            logger.warning(f"Error processing SSE chunk: {chunk_error}")
                            continue
        except Exception as sse_error:
            logger.error(f"Error fetching results from SSE stream: {sse_error}", exc_info=True)
        return profiles
