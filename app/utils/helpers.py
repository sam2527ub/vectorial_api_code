"""General helper functions."""
import logging
from typing import List, Dict, Optional
from datetime import datetime
from dateutil.parser import parse, ParserError
from fastapi import HTTPException
from app import database
from app.models.schemas import SearchFilters

logger = logging.getLogger(__name__)


def calculate_experience_years(experience_list: List[Dict]) -> float:
    """Calculates total years of experience, attempting to skip internships."""
    if not experience_list:
        return 0.0

    INTERN_KEYWORDS = ['intern', 'internship', 'trainee', 'apprentice', 'co-op']
    oldest_start_date = None

    # Iterate from oldest to newest to find first non-internship role
    for job in reversed(experience_list):
        title = job.get('title', {}).get('name', '').lower()
        if not any(k in title for k in INTERN_KEYWORDS):
            start_str = job.get('start_date')
            if start_str:
                try:
                    if len(start_str) == 4:
                        oldest_start_date = parse(f"{start_str}-01-01")
                    elif len(start_str) == 7:
                        oldest_start_date = parse(f"{start_str}-01")
                    else:
                        oldest_start_date = parse(start_str)
                    break
                except (ParserError, ValueError):
                    continue
    
    if not oldest_start_date:
        return 0.0

    # Calculate duration until now (or most recent end date)
    newest_end_str = experience_list[0].get('end_date')
    if newest_end_str:
        try:
            if len(newest_end_str) == 4:
                newest_end_date = parse(f"{newest_end_str}-12-31")
            elif len(newest_end_str) == 7:
                newest_end_date = parse(f"{newest_end_str}-28")
            else:
                newest_end_date = parse(newest_end_str)
        except:
            newest_end_date = datetime.now()
    else:
        newest_end_date = datetime.now()

    days = (newest_end_date - oldest_start_date).days
    return round(max(0, days / 365.25), 1)


def build_pdl_sql(f: SearchFilters) -> str:
    """Constructs the PDL SQL query based on filters."""
    parts = []

    def add_in_clause(field, values):
        if values:
            clean_vals = [v.replace("'", "''") for v in values]
            joined = ", ".join([f"'{v}'" for v in clean_vals])
            parts.append(f"{field} IN ({joined})")

    add_in_clause("job_title", f.titles)
    add_in_clause("skills", f.skills)
    add_in_clause("location_country", f.locations)
    add_in_clause("industry", f.industries)
    add_in_clause("job_company_size", f.company_sizes)
    add_in_clause("education.degrees", f.education_degrees)
    add_in_clause("job_title_levels", f.seniority_levels)

    # Conditional Role Logic
    if f.job_roles:
        field = "job_title_role" if f.role_search_type == "Current Role Only" else "experience.title.role"
        add_in_clause(field, f.job_roles)

    # Conditional Company Logic
    if f.company_names:
        field = "job_company_name" if f.company_search_type == "Current Company Only" else "experience.company.name"
        add_in_clause(field, f.company_names)

    if not parts:
        return "SELECT * FROM person"
    
    return f"SELECT * FROM person WHERE {' AND '.join(parts)}"


def ensure_db_available(db_type: str = "main") -> bool:
    """Check if database connection is available.
    
    Args:
        db_type: Either "main" or "audience"
    
    Returns:
        True if database is available, raises HTTPException otherwise
    """
    if db_type == "main":
        if not database.is_main_db_available():
            raise HTTPException(status_code=503, detail="Main database connection not available. Please set DATABASE_URL.")
        return True
    elif db_type == "audience":
        if not database.is_audience_db_available():
            raise HTTPException(status_code=503, detail="Audience database connection not available. Please set AUDIENCE_DATABASE_URL.")
        return True
    else:
        raise ValueError(f"Unknown db_type: {db_type}")


def normalize_linkedin_url(url: str) -> Optional[str]:
    """Normalize LinkedIn profile URLs for matching (strip scheme, www, query, trailing slash)."""
    if not url:
        return None
    url = url.strip().lower()
    if not url:
        return None
    # Ensure scheme for parsing
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if not parsed.netloc.endswith("linkedin.com"):
        return None

    # Keep only path without query/fragment
    path = parsed.path or ""
    # Remove multiple trailing slashes
    while path.endswith("/") and path != "/":
        path = path[:-1]
    # If path is empty, return None
    if not path:
        return None

    # Reconstruct canonical form without scheme/www/query
    return f"linkedin.com{path}"

