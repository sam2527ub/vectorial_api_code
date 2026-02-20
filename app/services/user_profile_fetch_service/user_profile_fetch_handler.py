"""Handler for User Profile Fetch service: Enrich Profiles Sync (POST /api/apify/enrich)."""
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from app.config import logger
from app.services.user_profile_fetch_service.config import UserProfileFetchConfig
from app.services.user_profile_fetch_service.clients.scraping.factory import get_scraping_client
from app.services.user_profile_fetch_service.utils import (
    build_url_to_original_profile_map,
    build_apify_url_to_result_map,
    merge_apify_result_into_profile,
    is_valid_enriched_profile,
    chunk_list,
)


class UserProfileFetchHandler:
    """Orchestrates sync Apify batch enrichment (enrich profiles with LinkedIn data)."""

    def __init__(self):
        self.config = UserProfileFetchConfig()
        self.scraping_client = get_scraping_client(self.config)

    def _validate(self, profiles: List[Dict[str, Any]]) -> None:
        if not self.scraping_client.is_configured():
            raise HTTPException(status_code=503, detail="Apify client not initialized")
        if not profiles:
            raise HTTPException(status_code=400, detail="No profiles provided")

    async def enrich_profiles_sync(
        self,
        profiles: List[Dict[str, Any]],
        enterprise_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Enrich profiles with Apify (sync blocking). Same behavior as POST /api/apify/enrich.
        Returns: profiles, total_found, filtered_count, total_profiles, message.
        """
        self._validate(profiles)
        linkedin_urls, url_to_profile_map = build_url_to_original_profile_map(profiles)
        if not linkedin_urls:
            raise HTTPException(
                status_code=400,
                detail="No valid LinkedIn URLs found in profiles",
            )

        batch_size = self.config.max_profiles_per_batch
        url_batches = chunk_list(linkedin_urls, batch_size)
        logger.info(
            f"Split {len(linkedin_urls)} profiles into {len(url_batches)} batches of max {batch_size}"
        )

        batch_runs: List[tuple] = []  # (batch_idx, run_id, url_batch)
        for batch_idx, url_batch in enumerate(url_batches):
            logger.info(
                f"Starting Apify batch {batch_idx + 1}/{len(url_batches)} with {len(url_batch)} profiles"
            )
            run_id = self.scraping_client.start_batch_run(url_batch)
            if run_id:
                batch_runs.append((batch_idx, run_id, url_batch))
                logger.info(f"Batch {batch_idx + 1} started: {run_id}")
            else:
                logger.error(f"Failed to start Apify batch {batch_idx + 1}")

        if not batch_runs:
            raise HTTPException(
                status_code=500,
                detail="Failed to start any Apify batch runs",
            )

        all_apify_results: List[Dict[str, Any]] = []
        for batch_idx, run_id, url_batch in batch_runs:
            logger.info(f"Waiting for batch {batch_idx + 1}/{len(batch_runs)} (run {run_id})...")
            status_result = await self.scraping_client.wait_for_run_completion(run_id)
            status = status_result.get("status", "")
            if status == "SUCCEEDED":
                dataset_id = status_result.get("dataset_id")
                if dataset_id:
                    batch_results = self.scraping_client.get_run_results(dataset_id)
                    all_apify_results.extend(batch_results)
                    logger.info(f"Batch {batch_idx + 1} completed: {len(batch_results)} profiles enriched")
                else:
                    logger.warning(f"Batch {batch_idx + 1} succeeded but no dataset ID")
            else:
                error = status_result.get("error", "Unknown error")
                logger.warning(f"Batch {batch_idx + 1} failed: {error}")

        apify_url_map = build_apify_url_to_result_map(all_apify_results)
        logger.info(
            f"Got {len(apify_url_map)} unique Apify results to match against {len(url_to_profile_map)} profiles"
        )

        enriched_profiles: List[Dict[str, Any]] = []
        filtered_profiles: List[Dict[str, Any]] = []
        for linkedin_url, original_profile in url_to_profile_map.items():
            enriched = merge_apify_result_into_profile(
                original_profile, linkedin_url, apify_url_map
            )
            if is_valid_enriched_profile(enriched):
                enriched_profiles.append(enriched)
            else:
                filtered_profiles.append({
                    "linkedin_url": linkedin_url,
                    "reason": "Profile not found or invalid on LinkedIn",
                })

        logger.info(
            f"Enriched {len(enriched_profiles)} profiles, filtered out {len(filtered_profiles)} invalid profiles"
        )
        return {
            "profiles": enriched_profiles,
            "total_found": len(enriched_profiles),
            "filtered_count": len(filtered_profiles),
            "total_profiles": len(linkedin_urls),
            "message": (
                f"Successfully enriched {len(enriched_profiles)} profiles. "
                f"{len(filtered_profiles)} invalid profiles were filtered out."
            ),
        }
