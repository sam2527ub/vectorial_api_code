"""Pydantic models for request/response schemas."""
from typing import List, Optional
from pydantic import BaseModel, Field


class EnrichRequest(BaseModel):
    job_title: str = Field(..., example="Machine Learning Engineer")


class DescriptionRequest(BaseModel):
    description: str = Field(..., example="Software Engineers in SF working at Series B companies")


class SearchFilters(BaseModel):
    titles: List[str] = []
    skills: List[str] = []
    locations: List[str] = []
    industries: List[str] = []
    company_names: List[str] = []
    company_sizes: List[str] = []
    education_degrees: List[str] = []
    seniority_levels: List[str] = []
    job_roles: List[str] = []
    
    # Search Modes
    role_search_type: str = "Current Role Only"  # Options: "Current Role Only", "Entire History"
    company_search_type: str = "Current Company Only"  # Options: "Current Company Only", "Entire History"
    
    limit: int = 10
    experience_bucket: str = "Any"  # Handled in Python after API fetch


class Cookie(BaseModel):
    domain: str
    expirationDate: Optional[float] = None
    hostOnly: bool = False
    httpOnly: bool = False
    name: str
    path: str = "/"
    sameSite: Optional[str] = None
    secure: bool = True
    session: bool = False
    storeId: Optional[str] = None
    value: str


class ScrapeRequest(BaseModel):
    linkedin_urls: List[str] = Field(..., min_items=1, description="List of LinkedIn profile URLs to scrape")
    max_posts: int = Field(25, ge=1, le=100, description="Maximum number of posts to scrape per profile")
    cookies: List[Cookie] = Field(..., min_items=1, description="List of cookie objects for authentication")
    user_agent: str = Field(..., description="User agent string to use for scraping")
    audience_room_id: Optional[str] = Field(
        None,
        description="If provided, scraped posts will be auto-mapped to this audience room when the job completes",
    )
    enterpriseName: Optional[str] = Field(None, description="Enterprise name (gamma, app, entelligence, beta). If not provided, uses default audience database.")


class AudienceProfilePayload(BaseModel):
    name: str = Field(..., description="Full name of the profile")
    age: Optional[int] = Field(None, description="Age if available")
    current_company: Optional[str] = Field(None, description="Current company")
    current_location: Optional[str] = Field(None, description="Current location")
    total_years_experience: Optional[float] = Field(None, description="Total years of experience")
    industry: Optional[str] = Field(None, description="Industry")
    education: Optional[str] = Field(None, description="Education summary")
    linkedin_profile_url: str = Field(..., description="LinkedIn profile URL")
    jobTitle: Optional[str] = Field(None, description="Job title from Apify enrichment")
    headline: Optional[str] = Field(None, description="Headline from Apify enrichment")
    about: Optional[str] = Field(None, description="About section from Apify enrichment")


class CreateAudienceRoomRequest(BaseModel):
    audience_room_name: str = Field(..., description="Name of the audience room")
    audience_description: str = Field(..., description="Plain-text description for the audience room")
    profiles: List[AudienceProfilePayload] = Field(..., min_items=1, description="Profiles to attach to this audience room")
    userId: str = Field(..., description="User ID associated with this audience room")
    query: Optional[str] = Field(None, description="Optional: The parallel search query used to find these profiles")
    search_results: Optional[List[dict]] = Field(None, description="Optional: The full parallel search results/indexes to store")
    source: Optional[str] = Field(None, description="Optional: Source of the audience room creation")
    enterpriseName: Optional[str] = Field(None, description="Enterprise name (gamma, app, entelligence, beta). If not provided, uses default audience database.")


class ParallelSearchRequest(BaseModel):
    query: str = Field(..., description="Search query string for Parallel FindAll")
    model: str = Field("core", description="Model to use: 'core' or 'base'")
    match_limit: int = Field(100, ge=1, le=1000, description="Maximum number of matches to return")


class ParallelSearchPreviewRequest(BaseModel):
    query: str = Field(..., description="Search query string for Parallel FindAll preview")


class ApifySearchRequest(BaseModel):
    """Request for Apify LinkedIn Company Employees Scraper (filter-based)."""

    companies: List[str] = Field(
        ...,
        min_length=1,
        description="List of LinkedIn company URLs or company names (required)",
    )
    company_batch_mode: Optional[str] = Field(
        "one_by_one",
        description="'one_by_one' (up to 1000 companies) or 'all_at_once' (max 10)",
    )
    job_titles: Optional[List[str]] = Field(
        None,
        description="List of job titles to filter (strict search)",
    )
    locations: Optional[List[str]] = Field(
        None,
        description="List of locations (e.g. New York, London)",
    )
    profile_scraper_mode: Optional[str] = Field(
        "Short ($4 per 1k)",
        description="'Short ($4 per 1k)' | 'Full ($8 per 1k)' | 'Full + email search ($12 per 1k)'",
    )
    max_items: Optional[int] = Field(
        250,
        ge=0,
        description="Max profiles to scrape; 0 = all (up to 2500 per query)",
    )
    start_page: Optional[int] = Field(None, ge=1, description="Start from this search results page")
    recently_changed_jobs: Optional[bool] = Field(None, description="Filter by recently changed jobs")
    general_search_query: Optional[str] = Field(None, description="Fuzzy search query")
    industry_ids: Optional[List[int]] = Field(None, description="LinkedIn industry IDs")
    years_at_company: Optional[List[int]] = Field(None, description="Years at company filter")

    class Config:
        # Allow Apify actor input field names (snake_case in API, camelCase when sending to Apify)
        extra = "allow"

