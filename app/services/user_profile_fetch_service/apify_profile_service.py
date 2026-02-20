"""Apify LinkedIn profile fetch (single URL) and field extraction. Part of User Profile Fetch service."""
import asyncio
from typing import Optional, Dict, Any
from app.config import apify_client, PROFILE_SCRAPER_ACTOR_ID, logger


async def fetch_linkedin_profile_info(linkedin_url: str) -> Optional[Dict[str, Any]]:
    """
    Fetch LinkedIn profile information from Apify scraper for a given LinkedIn URL.

    Args:
        linkedin_url: LinkedIn profile URL to scrape

    Returns:
        Dictionary with profile information or None if failed
    """
    if not apify_client:
        logger.warning("Apify client not initialized, skipping profile fetch")
        return None

    try:
        # Normalize URL for Apify
        url = linkedin_url.strip()
        if not url.startswith("http://") and not url.startswith("https://"):
            url = "https://" + url
        if url.startswith("https://linkedin.com") and not url.startswith("https://www.linkedin.com"):
            url = url.replace("https://linkedin.com", "https://www.linkedin.com")

        # Prepare input for Apify actor
        run_input = {
            "profileUrls": [url],
            "proxy": {"useApifyProxy": True, "apifyProxyGroups": []}
        }

        logger.info(f"Starting Apify profile scraper for: {url}")

        # Start the Apify actor run
        run = apify_client.actor(PROFILE_SCRAPER_ACTOR_ID).start(run_input=run_input)
        run_data = None
        if isinstance(run, dict):
            run_data = run.get("data", run)
        if not run_data or not isinstance(run_data, dict):
            logger.error(f"Apify start returned unexpected response for {url}")
            return None

        apify_run_id = run_data.get('id')
        if not apify_run_id:
            logger.error(f"Apify start did not return a run id for {url}")
            return None

        logger.info(f"Apify run started: {apify_run_id} for {url}")

        # Wait for the run to complete (with timeout)
        max_wait_time = 120  # 2 minutes max wait
        wait_interval = 2  # Check every 2 seconds
        elapsed = 0

        while elapsed < max_wait_time:
            await asyncio.sleep(wait_interval)
            elapsed += wait_interval

            run_status = apify_client.run(apify_run_id).get()
            status = run_status.get("status", "").upper()

            if status == "SUCCEEDED":
                # Fetch the dataset items
                dataset_id = run_status.get("defaultDatasetId")
                if dataset_id:
                    # Use iterate_items() to get the first item
                    for item in apify_client.dataset(dataset_id).iterate_items():
                        logger.info(f"Successfully fetched profile info for {url}")
                        return item
                    # If no items found
                    logger.warning(f"No data returned from Apify for {url}")
                    return None
                else:
                    logger.warning(f"No dataset ID in Apify run for {url}")
                    return None
            elif status in ["FAILED", "ABORTED", "TIMED-OUT"]:
                error_msg = run_status.get("statusMessage", "Unknown error")
                logger.error(f"Apify run failed for {url}: {error_msg}")
                return None

        # Timeout
        logger.warning(f"Apify run timed out for {url} after {max_wait_time} seconds")
        return None

    except Exception as e:
        logger.error(f"Error fetching profile info from Apify for {linkedin_url}: {e}")
        return None


def extract_apify_profile_fields(apify_data: dict) -> dict:
    """
    Extract only the required fields from Apify profile data.

    Args:
        apify_data: Full Apify profile data dictionary

    Returns:
        Dictionary with only the required fields
    """
    if not apify_data or not isinstance(apify_data, dict):
        return {}

    # Extract education data - get degree and institution
    educations = []
    for edu in apify_data.get("educations", []):
        edu_entry = {
            "institution": edu.get("title", ""),
            "degree": edu.get("subtitle", ""),
            "period": edu.get("period", {})
        }
        educations.append(edu_entry)

    # Extract the required fields
    extracted = {
        "fullName": apify_data.get("fullName", ""),
        "jobTitle": apify_data.get("jobTitle", ""),
        "currentCompany": apify_data.get("companyName", ""),
        "companyIndustry": apify_data.get("companyIndustry", ""),
        "currentLocation": apify_data.get("jobLocation") or apify_data.get("addressWithCountry", ""),
        "totalYearsExperience": apify_data.get("totalExperienceYears", 0),
        "education": educations,
        "about": apify_data.get("about", ""),
        "headline": apify_data.get("headline", "")
    }

    return extracted
