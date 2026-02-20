"""Repository for ScrapeJob (audience DB). Wraps app.database."""
from typing import Any, Dict, List, Optional

from app import database


def create_job(
    linkedin_urls: List[str],
    max_posts: int,
    audience_room_id: Optional[str] = None,
    enterprise_name: Optional[str] = None,
):
    """Create a ScrapeJob. Returns the job (ScrapeJob)."""
    return database.create_scrape_job(
        linkedin_urls=linkedin_urls,
        max_posts=max_posts,
        audience_room_id=audience_room_id,
        enterprise_name=enterprise_name,
    )


def find_job_by_id(job_id: str, enterprise_name: Optional[str] = None):
    """Find ScrapeJob by id. Returns job or None."""
    return database.find_scrape_job_by_id(job_id=job_id, enterprise_name=enterprise_name)


def update_job(
    job_id: str,
    data: Dict[str, Any],
    enterprise_name: Optional[str] = None,
):
    """Update ScrapeJob. Returns updated job or None."""
    return database.update_scrape_job(
        job_id=job_id,
        data=data,
        enterprise_name=enterprise_name,
    )
