import os
import json
import logging
import uuid
import asyncio
import re
from typing import List, Optional, Dict, Any
from datetime import datetime
from dateutil.parser import parse, ParserError

# FastAPI Imports
from fastapi import FastAPI, HTTPException, Body, Path, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, root_validator

# Third-party Clients
from dotenv import load_dotenv

# --- 1. CONFIGURATION & SETUP ---
load_dotenv()

# Logging setup (needed early for Prisma generation)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Additional Third-party Clients
from peopledatalabs import PDLPY
from apify_client import ApifyClient
from openai import OpenAI
import boto3
from groq import Groq

# Database module using psycopg2 (replaces Prisma to avoid Vercel deployment issues)
import db as database
logger.info("Database module imported (using psycopg2)")

# Initialize Clients
pdl_client = None
apify_client = None
openai_client = None
groq_client = None
dynamodb_resource = None
s3_client = None
s3_bucket = os.getenv("AUDIENCE_BUCKET_NAME") or os.getenv("VECTOR_BUCKET_NAME")
s3_region = os.getenv("AWS_REGION", "us-west-2")

# Database availability flags (using psycopg2 module)
main_db_available = database.is_main_db_available()
audience_db_available = database.is_audience_db_available()
logger.info(f"Main DB available: {main_db_available}, Audience DB available: {audience_db_available}")

try:
    pdl_client = PDLPY(api_key=os.getenv("PDL_API_KEY"))
except Exception as e:
    logger.error(f"Failed to initialize PDL client: {e}")

try:
    apify_client = ApifyClient(os.getenv("APIFY_API_TOKEN"))
except Exception as e:
    logger.error(f"Failed to initialize Apify client: {e}")

try:
    # Initialize OpenAI client - handle version compatibility
    openai_api_key = os.getenv("OPENAI_API_KEY")
    if openai_api_key:
        # Try without any extra parameters first
        openai_client = OpenAI(api_key=openai_api_key)
    else:
        logger.warning("OPENAI_API_KEY not set")
except Exception as e:
    logger.error(f"Failed to initialize OpenAI client: {e}")
    openai_client = None

try:
    # Initialize Groq client for fast LLM inference
    groq_api_key = os.getenv("GROQ_API_KEY")
    groq_model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")  # Default model, can be overridden
    if groq_api_key:
        groq_client = Groq(api_key=groq_api_key)
        logger.info(f"Groq client initialized successfully with model: {groq_model}")
    else:
        logger.warning("GROQ_API_KEY not set")
except Exception as e:
    logger.error(f"Failed to initialize Groq client: {e}")
    groq_client = None

try:
    # Optional: DynamoDB
    dynamodb_resource = boto3.resource('dynamodb', region_name=os.getenv('AWS_REGION', 'us-west-2'))
except Exception as e:
    logger.warning(f"DynamoDB not initialized: {e}")

try:
    # S3 client for audience assets
    s3_client = boto3.client("s3", region_name=s3_region) if s3_bucket else None
    if s3_client and s3_bucket:
        logger.info(f"S3 client initialized for bucket {s3_bucket}")
    else:
        logger.warning("S3 bucket not configured; audience uploads will be disabled")
except Exception as e:
    logger.error(f"Failed to initialize S3 client: {e}")
    s3_client = None

# Lifespan event handlers (replaces deprecated on_event)
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup - verify database connections
    global main_db_available, audience_db_available
    
    if database.is_main_db_available():
        if database.check_main_db_connection():
            logger.info("Main database connection verified successfully")
            main_db_available = True
        else:
            logger.warning("Main database connection check failed")
            main_db_available = False
    else:
        logger.warning("Main database not configured - scraping endpoints will not work")
        main_db_available = False
    
    if database.is_audience_db_available():
        if database.check_audience_db_connection():
            logger.info("Audience database connection verified successfully")
            audience_db_available = True
        else:
            logger.warning("Audience database connection check failed")
            audience_db_available = False
    else:
        logger.warning("Audience database not configured - audience endpoints will be disabled")
        audience_db_available = False
    
    yield
    
    # Shutdown - close connection pools
    database.close_pools()
    logger.info("Database connection pools closed")

# Create FastAPI app with lifespan
app = FastAPI(
    title="Profile Engine API",
    description="Backend for PDL Enrichment, Search, and LinkedIn Scraping",
    version="1.0.0",
    lifespan=lifespan
)

# Enable CORS (Allows your React frontend to talk to this Python backend)
# SECURITY NOTE: In production, replace "*" with your specific frontend domain(s)
# Example: allow_origins=["https://yourdomain.com", "https://www.yourdomain.com"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: In production, specify your frontend domain (e.g., ["https://yourdomain.com"])
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Constants
POST_SCRAPER_ACTOR_ID = "curious_coder/linkedin-post-search-scraper"

# --- 2. DATA MODELS (Request/Response Schemas) ---

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


class AudienceProfilePayload(BaseModel):
    name: str = Field(..., description="Full name of the profile")
    age: Optional[int] = Field(None, description="Age if available")
    current_company: Optional[str] = Field(None, description="Current company")
    current_location: Optional[str] = Field(None, description="Current location")
    total_years_experience: Optional[float] = Field(None, description="Total years of experience")
    industry: Optional[str] = Field(None, description="Industry")
    education: Optional[str] = Field(None, description="Education summary")
    linkedin_profile_url: str = Field(..., description="LinkedIn profile URL")


class CreateAudienceRoomRequest(BaseModel):
    audience_room_name: str = Field(..., description="Name of the audience room")
    audience_description: str = Field(..., description="Plain-text description for the audience room")
    profiles: List[AudienceProfilePayload] = Field(..., min_items=1, description="Profiles to attach to this audience room")
    userId: str = Field(..., description="User ID associated with this audience room")


class UpdateProfilePostsRequest(BaseModel):
    posts: Any = Field(..., description="Posts JSON to store for this profile")


class BatchPostsRequest(BaseModel):
    job_id: Optional[str] = Field(
        None,
        description="Scrape job ID to load posts from the ScrapeJob table (must be COMPLETED)",
    )
    posts: Optional[List[Any]] = Field(
        None,
        description="Full posts dataset (list of records with inputUrl identifying the profile)",
    )

    @root_validator
    def ensure_source(cls, values):
        if not values.get("posts") and not values.get("job_id"):
            raise ValueError("Provide either posts or job_id")
        return values


class RunClassifierRequest(BaseModel):
    audienceRoomId: str = Field(..., description="ID of the audience room containing profiles to classify")
    classifierId: str = Field(..., description="ID of the classifier to use for classification")

# --- 3. HELPER FUNCTIONS ---

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
                    if len(start_str) == 4: oldest_start_date = parse(f"{start_str}-01-01")
                    elif len(start_str) == 7: oldest_start_date = parse(f"{start_str}-01")
                    else: oldest_start_date = parse(start_str)
                    break
                except (ParserError, ValueError):
                    continue
    
    if not oldest_start_date:
        return 0.0

    # Calculate duration until now (or most recent end date)
    newest_end_str = experience_list[0].get('end_date')
    if newest_end_str:
        try:
            if len(newest_end_str) == 4: newest_end_date = parse(f"{newest_end_str}-12-31")
            elif len(newest_end_str) == 7: newest_end_date = parse(f"{newest_end_str}-28")
            else: newest_end_date = parse(newest_end_str)
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


def upload_json_to_s3(key: str, data: Dict[str, Any]) -> str:
    """Upload JSON payload to S3 and return a public URL."""
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")
    try:
        s3_client.put_object(
            Bucket=s3_bucket,
            Key=key,
            Body=json.dumps(data),
            ContentType="application/json",
        )
        return f"https://{s3_bucket}.s3.{s3_region}.amazonaws.com/{key}"
    except Exception as e:
        logger.error(f"S3 upload failed for {key}: {e}")
        raise HTTPException(status_code=500, detail="Failed to upload to S3")

def to_json(data: Any) -> Any:
    """Return data as-is (JSON handling is done in the db module)."""
    return data


def generate_presigned_get_url(key: str, expires_in: int = 3600) -> Optional[str]:
    """
    Generate a presigned GET URL for an S3 object.
    
    Default expiration: 1 hour (3600 seconds) for production security.
    Presigned URLs are time-limited and provide secure access to private S3 objects.
    
    Security considerations:
    - URLs expire after the specified time, preventing long-term unauthorized access
    - URLs are cryptographically signed, making them tamper-proof
    - Access is controlled through the API layer (add authentication/authorization as needed)
    """
    if not s3_client or not s3_bucket:
        return None
    try:
        return s3_client.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": s3_bucket, "Key": key},
            ExpiresIn=expires_in,
        )
    except Exception as e:
        logger.error(f"Failed to generate presigned URL for {key}: {e}")
        return None


def extract_s3_key_from_url(s3_url: Optional[str]) -> Optional[str]:
    """Extract S3 key from a full S3 URL.
    
    Examples:
    - https://bucket.s3.region.amazonaws.com/path/to/file.json -> path/to/file.json
    - https://bucket.s3.amazonaws.com/path/to/file.json -> path/to/file.json
    """
    if not s3_url:
        return None
    try:
        from urllib.parse import urlparse
        parsed = urlparse(s3_url)
        # Remove leading slash from path
        key = parsed.path.lstrip('/')
        return key if key else None
    except Exception as e:
        logger.error(f"Failed to extract S3 key from URL {s3_url}: {e}")
        return None


def generate_presigned_url_from_s3_url(s3_url: Optional[str], expires_in: int = 3600) -> Optional[str]:
    """
    Generate a presigned URL from a stored S3 URL by extracting the key.
    
    Default expiration: 1 hour (3600 seconds) for production security.
    """
    if not s3_url:
        return None
    key = extract_s3_key_from_url(s3_url)
    if not key:
        return None
    return generate_presigned_get_url(key, expires_in)


def fetch_json_from_s3(key: str) -> Dict[str, Any]:
    """Fetch JSON data from S3 by key."""
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")
    try:
        response = s3_client.get_object(Bucket=s3_bucket, Key=key)
        content = response['Body'].read().decode('utf-8')
        return json.loads(content)
    except s3_client.exceptions.NoSuchKey as e:
        raise HTTPException(status_code=404, detail=f"S3 object not found: {key}")
    except Exception as e:
        logger.error(f"Failed to fetch JSON from S3 for key {key}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch data from S3: {str(e)}")


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


async def process_posts_and_update_profiles(
    dataset_client,
    job_id: str,
    audience_room_id: Optional[str] = None,
    linkedin_urls: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Process posts from Apify dataset, split by profile, upload to S3, and update AudienceProfile table.
    
    Args:
        dataset_client: Apify dataset client to iterate items from
        job_id: Scrape job ID
        audience_room_id: Optional audience room ID to filter profiles
        linkedin_urls: Optional list of LinkedIn URLs that were scraped (for matching)
    
    Returns:
        Dictionary with processing results (posts_found, profiles_updated, profiles_missing, etc.)
    """
    if not database.is_audience_db_available():
        logger.warning("Audience database not available, skipping profile updates")
        return {"posts_found": 0, "profiles_updated": 0, "profiles_missing": 0}
    
    if not s3_client or not s3_bucket:
        logger.warning("S3 not configured, skipping profile updates")
        return {"posts_found": 0, "profiles_updated": 0, "profiles_missing": 0}
    
    # Fetch profiles - either from specific room or all profiles if URLs provided
    if audience_room_id:
        # Get profiles from specific audience room
        profiles = database.find_audience_profiles(audience_room_id=audience_room_id)
    elif linkedin_urls:
        # Get all profiles that match any of the scraped URLs
        # We'll filter by matching normalized URLs
        all_profiles = database.find_audience_profiles(all_profiles=True)
        # Normalize scraped URLs for matching
        normalized_scraped_urls = set()
        for url in linkedin_urls:
            norm_url = normalize_linkedin_url(url)
            if norm_url:
                normalized_scraped_urls.add(norm_url)
        
        # Filter profiles that match scraped URLs
        profiles = []
        for p in all_profiles:
            norm_profile_url = normalize_linkedin_url(p.linkedinUrl)
            if norm_profile_url and norm_profile_url in normalized_scraped_urls:
                profiles.append(p)
    else:
        # No room ID and no URLs - can't match profiles
        logger.warning("No audience room ID or LinkedIn URLs provided, cannot match profiles")
        return {"posts_found": 0, "profiles_updated": 0, "profiles_missing": 0}
    
    if not profiles:
        logger.warning(f"No profiles found to match posts against")
        return {"posts_found": 0, "profiles_updated": 0, "profiles_missing": 0}
    
    # Build profile lookup by normalized URL
    profile_by_url: Dict[str, Dict[str, Any]] = {}
    for p in profiles:
        norm_url = normalize_linkedin_url(p.linkedinUrl)
        if norm_url:
            profile_by_url[norm_url] = {
                "id": p.id,
                "profileName": p.profileName,
                "linkedinUrl": p.linkedinUrl,
                "audienceRoomId": p.audienceRoomId,
            }
    
    # Group posts by profile
    posts_acc: Dict[str, List[Any]] = {p["id"]: [] for p in profile_by_url.values()}
    total_items = 0
    
    for item in dataset_client.iterate_items():
        total_items += 1
        if not isinstance(item, dict):
            continue
        input_url = normalize_linkedin_url(str(item.get("inputUrl", "")))
        if not input_url:
            continue
        target = profile_by_url.get(input_url)
        if target:
            posts_acc[target["id"]].append(item)
    
    # Upload posts to S3 and update database
    updated = []
    missing = []
    
    for p in profile_by_url.values():
        pid = p["id"]
        posts_for_profile = posts_acc.get(pid, [])
        if not posts_for_profile:
            missing.append({
                "profile_id": pid,
                "profile_name": p["profileName"],
                "linkedin_url": p["linkedinUrl"],
                "reason": "no_posts_found"
            })
            continue
        
        # Use the profile's audience room ID for S3 path
        room_id = p["audienceRoomId"]
        posts_key = f"audiences/{room_id}/profiles/{pid}/posts.json"
        posts_url = upload_json_to_s3(
            posts_key,
            {
                "profile_id": pid,
                "audience_room_id": room_id,
                "linkedin_profile_url": p["linkedinUrl"],
                "posts": posts_for_profile,
            },
        )
        
        try:
            database.update_audience_profile(pid, {"postsS3Url": posts_url})
            updated.append({
                "profile_id": pid,
                "profile_name": p["profileName"],
                "linkedin_url": p["linkedinUrl"],
                "audience_room_id": room_id,
                "posts_s3_url": posts_url,
            })
        except Exception as e:
            logger.error(f"Error updating posts for profile {pid}: {e}")
            missing.append({
                "profile_id": pid,
                "profile_name": p["profileName"],
                "linkedin_url": p["linkedinUrl"],
                "reason": "db_update_failed"
            })
    
    return {
        "posts_found": total_items,
        "profiles_updated": len(updated),
        "profiles_missing": len(missing),
        "updated": updated,
        "missing": missing,
    }

# --- 4. API ENDPOINTS ---

@app.get("/")
def health_check():
    """Simple health check endpoint."""
    return {"status": "ok", "message": "Backend is running"}

# === STEP 1: ENRICH JOB TITLE ===
@app.post("/api/v1/enrich")
async def enrich_job_title(payload: EnrichRequest):
    try:
        logger.info(f"Enriching title: {payload.job_title}")
        response = pdl_client.job_title(job_title=payload.job_title).json()
        
        if response.get("status") == 200:
            return response.get('data')
        
        raise HTTPException(status_code=400, detail=response.get('error', 'PDL API Error'))
    except Exception as e:
        logger.error(f"Enrichment error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# === STEP 2: NLP TO FILTERS ===
@app.post("/api/v1/extract-filters")
async def extract_filters(payload: DescriptionRequest):
    if not openai_client:
        raise HTTPException(status_code=500, detail="OpenAI client not initialized. Please check OPENAI_API_KEY.")

    prompt = f"""
    Extract search filters from: "{payload.description}"
    
    Respond in JSON format only with these keys:
    - job_titles (list of strings)
    - skills (list of strings)
    - locations (list of countries/cities)
    - company_names (list of strings)
    - industries (list of strings)
    - company_sizes (list of strings like "1-10", "11-50", "10000+")
    - education_degrees (list of strings like "Bachelors", "Masters")
    - seniority_levels (list of strings)
    - job_roles (list of strings)
    - role_search_type ("Current Role Only" or "Entire History")
    - company_search_type ("Current Company Only" or "Entire History")
    """
    
    try:
        completion = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You extract structured job search parameters from text to JSON."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"}
        )
        return json.loads(completion.choices[0].message.content)
    except Exception as e:
        logger.error(f"OpenAI error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# === STEP 3: SEARCH PROFILES ===
@app.post("/api/v1/search")
async def search_profiles(payload: SearchFilters):
    """
    Search for profiles using PDL API.
    Returns filtered profile information with only:
    - name: Full name of the person
    - age: Calculated from birth_date
    - current_company: Current job company name
    - current_location: Current location
    - total_years_experience: Calculated years of experience (excluding internships)
    - industry: Industry
    - education: Most recent/highest education (e.g., "Bachelors from Stanford University (Computer Science)")
    - linkedin_profile_url: LinkedIn profile URL
    
    Note: If searching for tech roles (engineer, developer, etc.) without specifying industry,
    results may include engineers working in non-tech industries (retail, real estate, etc.).
    To get more relevant results, add industry filter: ["Technology", "Computer Software", "Internet"]
    """
    sql_query = build_pdl_sql(payload)
    logger.info(f"Executing Search SQL: {sql_query}")

    params = {
        'sql': sql_query,
        'dataset': 'resume',
        'size': payload.limit,
        'pretty': True
    }

    try:
        response = pdl_client.person.search(**params).json()
        data = response.get('data', [])
        
        # Post-processing: Calculate Experience Years and filter to only required fields
        processed_profiles = []
        for person in data:
            # Calculate experience years
            years = calculate_experience_years(person.get('experience', []))
            
            # Calculate age - try multiple sources
            age = None
            
            # Method 1: Try birth_date (most accurate)
            if person.get('birth_date'):
                try:
                    birth_date = parse(person.get('birth_date'))
                    age = (datetime.now() - birth_date).days // 365
                except (ParserError, ValueError, TypeError):
                    pass
            
            # Method 2: Try inferred_age if available (PDL sometimes provides this)
            if age is None and person.get('inferred_age'):
                try:
                    age = int(person.get('inferred_age'))
                except (ValueError, TypeError):
                    pass
            
            # Method 3: Estimate from education graduation year (less accurate)
            if age is None and person.get('education'):
                try:
                    # Get most recent graduation year
                    graduation_years = []
                    for edu in person.get('education', []):
                        end_date = edu.get('end_date')
                        if end_date:
                            # Try to extract year
                            if len(end_date) >= 4:
                                year = int(end_date[:4])
                                graduation_years.append(year)
                    
                    if graduation_years:
                        # Assume typical graduation age: 22 for Bachelor's, 24 for Master's
                        most_recent_graduation = max(graduation_years)
                        years_since_graduation = datetime.now().year - most_recent_graduation
                        # Estimate: graduated at 22-24, add years since
                        estimated_age = 23 + years_since_graduation
                        if 18 <= estimated_age <= 80:  # Reasonable age range
                            age = estimated_age
                except (ValueError, TypeError, KeyError):
                    pass
            
            # Extract education information
            education_info = None
            if person.get('education') and len(person.get('education', [])) > 0:
                # Get the most recent/highest level education
                education_list = person.get('education', [])
                # Sort by end_date (most recent first) or start_date, prioritizing higher degrees
                def get_education_priority(edu):
                    degrees = edu.get('degrees', [])
                    degree_priority = {
                        'PhD': 4, 'Doctorate': 4, 'Ph.D.': 4,
                        'Masters': 3, 'Master': 3, 'Master of Science': 3, 'Master of Arts': 3,
                        'Bachelors': 2, 'Bachelor': 2, 'Bachelor of Science': 2, 'Bachelor of Arts': 2,
                        'Associates': 1, 'Associate': 1
                    }
                    max_priority = 0
                    for deg in degrees:
                        for key, priority in degree_priority.items():
                            if key.lower() in str(deg).lower():
                                max_priority = max(max_priority, priority)
                                break
                    return (max_priority, edu.get('end_date') or edu.get('start_date') or '0000')
                
                sorted_education = sorted(education_list, key=get_education_priority, reverse=True)
                most_recent_edu = sorted_education[0]
                
                # Format education string
                degrees = most_recent_edu.get('degrees', [])
                school = most_recent_edu.get('school', {})
                school_name = school.get('name', '') if isinstance(school, dict) else str(school) if school else ''
                majors = most_recent_edu.get('majors', [])
                
                # Clean and normalize degrees (remove duplicates and verbose forms)
                cleaned_degrees = []
                degree_seen = set()
                for deg in degrees:
                    deg_str = str(deg).strip()
                    # Normalize common degree names
                    deg_lower = deg_str.lower()
                    if 'bachelor' in deg_lower and 'bachelors' not in deg_lower:
                        deg_str = 'Bachelors'
                    elif 'master' in deg_lower and 'masters' not in deg_lower:
                        deg_str = 'Masters'
                    elif 'phd' in deg_lower or 'doctorate' in deg_lower:
                        deg_str = 'PhD'
                    
                    # Avoid duplicates
                    if deg_str.lower() not in degree_seen:
                        cleaned_degrees.append(deg_str)
                        degree_seen.add(deg_str.lower())
                
                # Build education string: "Degree from School (Major)" or "School (Major)" if no degree
                edu_parts = []
                
                # Add degree if available
                if cleaned_degrees:
                    # Use the highest/most common degree
                    degree = cleaned_degrees[0]
                    edu_parts.append(degree)
                
                # Add school
                if school_name:
                    if edu_parts:
                        edu_parts.append(f"from {school_name}")
                    else:
                        # If no degree, just use school name
                        edu_parts.append(school_name)
                
                # Add majors
                if majors:
                    majors_str = ', '.join(majors)
                    edu_parts.append(f"({majors_str})")
                
                education_info = ' '.join(edu_parts) if edu_parts else None
            
            # Extract only required fields
            profile = {
                "name": person.get('full_name') or f"{person.get('first_name', '')} {person.get('last_name', '')}".strip() or None,
                "age": age,
                "current_company": person.get('job_company_name'),
                "current_location": person.get('location_name'),
                "total_years_experience": years,  # Calculated field
                "industry": person.get('industry'),
                "education": education_info,
                "linkedin_profile_url": person.get('linkedin_url')
            }
            
            processed_profiles.append(profile)

        # Optional: Store search query stats in DynamoDB here if needed
        
        return {
            "count": len(processed_profiles),
            "sql_generated": sql_query,
            "profiles": processed_profiles
        }
    except Exception as e:
        logger.error(f"Search error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# === STEP 3B: CREATE AUDIENCE ROOM WITH SELECTED PROFILES ===
@app.post("/api/v1/audience-rooms")
async def create_audience_room(payload: CreateAudienceRoomRequest):
    """
    Create an audience room, store its description and profile payloads in S3, and persist metadata in Postgres.
    - Stores audience description at: audiences/{audience_room_id}/description.json
    - Stores each profile payload (with summary=null) at: audiences/{audience_room_id}/profiles/{profile_id}/profile.json
    """
    ensure_db_available("audience")
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")

    room_id = str(uuid.uuid4())

    # Upload audience description to S3
    description_key = f"audiences/{room_id}/description.json"
    description_url = upload_json_to_s3(
        description_key,
        {
            "audience_room_id": room_id,
            "audience_room_name": payload.audience_room_name,
            "description": payload.audience_description,
        },
    )

    # Build profile records and upload payloads to S3 (summary starts as null)
    profile_creates = []
    for profile in payload.profiles:
        profile_id = str(uuid.uuid4())
        profile_key = f"audiences/{room_id}/profiles/{profile_id}/profile.json"
        profile_payload = {
            "profile_id": profile_id,
            "audience_room_id": room_id,
            "name": profile.name,
            "age": profile.age,
            "current_company": profile.current_company,
            "current_location": profile.current_location,
            "total_years_experience": profile.total_years_experience,
            "industry": profile.industry,
            "education": profile.education,
            "linkedin_profile_url": profile.linkedin_profile_url,
            "summary": None,
        }
        profile_url = upload_json_to_s3(profile_key, profile_payload)

        profile_creates.append(
            {
                "id": profile_id,
                "profileName": profile.name,
                "linkedinUrl": profile.linkedin_profile_url,
                "profileDescriptionS3Url": profile_url,
                "postsS3Url": None,
            }
        )

    try:
        room = database.create_audience_room(
            room_id=room_id,
            name=payload.audience_room_name,
            description_s3_url=description_url,
            user_id=payload.userId,
            profiles_data=profile_creates,
        )

        return {
            "audience_room_id": room.id,
            "audience_room_name": room.name,
            "description_s3_url": room.descriptionS3Url,
            "userId": room.userId,
            "profiles_created": len(room.profiles),
            "profiles": [
                {
                    "profile_id": p.id,
                    "profile_name": p.profileName,
                    "linkedin_url": p.linkedinUrl,
                    "profile_description_s3_url": p.profileDescriptionS3Url,
                    "posts_s3_url": p.postsS3Url,
                }
                for p in room.profiles
            ],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating audience room: {e}")
        raise HTTPException(status_code=500, detail="Failed to create audience room")


# === DELETE AUDIENCE ROOM ===
@app.delete("/api/v1/audience-rooms/{audience_room_id}")
async def delete_audience_room(audience_room_id: str):
    """
    Delete an audience room and all associated data.
    
    This endpoint:
    1. Deletes all S3 files associated with the audience room:
       - Audience room description: audiences/{audience_room_id}/description.json
       - All profile descriptions: audiences/{audience_room_id}/profiles/{profile_id}/profile.json
       - All profile posts: audiences/{audience_room_id}/profiles/{profile_id}/posts.json
    2. Deletes all profiles from the AudienceProfile table (cascade delete)
    3. Deletes the audience room from the AudienceRoom table
    
    Args:
        audience_room_id: The UUID of the audience room to delete
    
    Returns:
        Success message with details of deleted items
    """
    ensure_db_available("audience")
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")

    try:
        # Fetch audience room with all profiles
        audience_room = database.find_audience_room_by_id(audience_room_id, include_profiles=True)
        
        if not audience_room:
            raise HTTPException(status_code=404, detail=f"Audience room {audience_room_id} not found")
        
        profiles = audience_room.profiles
        profile_count = len(profiles)
        
        # Delete all S3 files associated with this audience room
        s3_prefix = f"audiences/{audience_room_id}/"
        deleted_s3_files = []
        
        try:
            # List all objects with the prefix
            paginator = s3_client.get_paginator('list_objects_v2')
            pages = paginator.paginate(Bucket=s3_bucket, Prefix=s3_prefix)
            
            # Collect all object keys to delete
            objects_to_delete = []
            for page in pages:
                if 'Contents' in page:
                    for obj in page['Contents']:
                        objects_to_delete.append({'Key': obj['Key']})
                        deleted_s3_files.append(obj['Key'])
            
            # Delete all objects in batch (max 1000 objects per request)
            if objects_to_delete:
                for i in range(0, len(objects_to_delete), 1000):
                    batch = objects_to_delete[i:i+1000]
                    s3_client.delete_objects(
                        Bucket=s3_bucket,
                        Delete={
                            'Objects': batch,
                            'Quiet': True
                        }
                    )
                logger.info(f"Deleted {len(objects_to_delete)} S3 objects for audience room {audience_room_id}")
            else:
                logger.warning(f"No S3 objects found for audience room {audience_room_id}")
                
        except Exception as e:
            logger.error(f"Error deleting S3 files for audience room {audience_room_id}: {e}")
            # Continue with database deletion even if S3 deletion fails
        
        # Delete all profiles from database first (explicit deletion for logging and clarity)
        # Note: Cascade delete is configured, so deleting the room would also delete profiles,
        # but we delete explicitly first to ensure proper cleanup order and logging
        if profiles:
            try:
                deleted_count = database.delete_audience_profiles_by_room(audience_room_id)
                logger.info(f"Deleted {deleted_count} profiles for audience room {audience_room_id}")
            except Exception as e:
                logger.error(f"Error deleting profiles for audience room {audience_room_id}: {e}")
                raise HTTPException(status_code=500, detail=f"Failed to delete profiles: {str(e)}")
        
        # Delete the audience room from database
        try:
            database.delete_audience_room(audience_room_id)
            logger.info(f"Deleted audience room {audience_room_id}")
        except Exception as e:
            logger.error(f"Error deleting audience room {audience_room_id}: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to delete audience room: {str(e)}")
        
        return {
            "message": f"Successfully deleted audience room {audience_room_id}",
            "audience_room_id": audience_room_id,
            "audience_room_name": audience_room.name,
            "profiles_deleted": profile_count,
            "s3_files_deleted": len(deleted_s3_files),
            "s3_files": deleted_s3_files
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting audience room {audience_room_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete audience room: {str(e)}")


# === STEP 3C: ATTACH POSTS TO AN AUDIENCE PROFILE ===
@app.post("/api/v1/audience-rooms/{audience_room_id}/profiles/{profile_id}/posts")
async def upload_profile_posts(audience_room_id: str, profile_id: str, payload: UpdateProfilePostsRequest):
    """
    Store scraped posts JSON for a profile in S3 and update the profile record.
    S3 path: audiences/{audience_room_id}/profiles/{profile_id}/posts.json
    """
    ensure_db_available("audience")
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")

    # Verify profile exists and belongs to the room
    profile = database.find_audience_profile_by_id(profile_id, include_room=True)
    if not profile or profile.audienceRoomId != audience_room_id:
        raise HTTPException(status_code=404, detail="Profile not found for given audience room")

    posts_key = f"audiences/{audience_room_id}/profiles/{profile_id}/posts.json"
    posts_url = upload_json_to_s3(posts_key, {"profile_id": profile_id, "audience_room_id": audience_room_id, "posts": payload.posts})

    try:
        updated = database.update_audience_profile(profile_id, {"postsS3Url": posts_url})
        return {
            "profile_id": updated.id,
            "audience_room_id": audience_room_id,
            "posts_s3_url": updated.postsS3Url,
        }
    except Exception as e:
        logger.error(f"Error updating posts for profile {profile_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to store posts")


# === STEP 3D: BATCH ATTACH POSTS FOR AN AUDIENCE ROOM ===
@app.post("/api/v1/audience-rooms/{audience_room_id}/posts")
async def upload_posts_batch(audience_room_id: str, payload: BatchPostsRequest):
    """
    Map scraped posts to profiles in an audience room, upload per-profile posts to S3,
    and update postsS3Url in the profile table.

    Source of posts:
    - payload.posts (if provided), or
    - payload.job_id -> loads ScrapeJob.result.data when the job is COMPLETED.
    """
    ensure_db_available("audience")
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")

    # Resolve posts source (payload or scrape job)
    source_posts: List[Any] = []
    if payload.posts:
        source_posts = payload.posts
    elif payload.job_id:
        ensure_db_available("main")
        job = database.find_scrape_job_by_id(payload.job_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"Scrape job {payload.job_id} not found")
        if job.status != "COMPLETED" or not job.result:
            raise HTTPException(
                status_code=409,
                detail=f"Scrape job {payload.job_id} is not completed yet. Current status: {job.status}",
            )

        # Extract data from job.result; expect {"data": [...]} or a list directly
        result_data = job.result
        if isinstance(result_data, dict):
            source_posts = result_data.get("data") or result_data.get("posts") or result_data.get("items") or []
        else:
            source_posts = result_data

        if not isinstance(source_posts, list):
            raise HTTPException(status_code=500, detail="Scrape job result format is invalid; expected a list of posts")

    # Fetch profiles in the room
    profiles = database.find_audience_profiles(audience_room_id=audience_room_id)
    if not profiles:
        raise HTTPException(status_code=404, detail="No profiles found for this audience room")

    # Group posts by normalized inputUrl
    posts_by_url: Dict[str, List[Any]] = {}
    for item in source_posts:
        if not isinstance(item, dict):
            continue
        input_url = normalize_linkedin_url(str(item.get("inputUrl", "")))
        if not input_url:
            continue
        posts_by_url.setdefault(input_url, []).append(item)

    updated = []
    missing = []
    for p in profiles:
        norm_profile_url = normalize_linkedin_url(p.linkedinUrl)
        if not norm_profile_url:
            missing.append({"profile_id": p.id, "profile_name": p.profileName, "reason": "missing_linkedin_url"})
            continue

        posts_for_profile = posts_by_url.get(norm_profile_url, [])
        if not posts_for_profile:
            missing.append({"profile_id": p.id, "profile_name": p.profileName, "reason": "no_posts_found"})
            continue

        posts_key = f"audiences/{audience_room_id}/profiles/{p.id}/posts.json"
        posts_url = upload_json_to_s3(
            posts_key,
            {
                "profile_id": p.id,
                "audience_room_id": audience_room_id,
                "linkedin_profile_url": p.linkedinUrl,
                "posts": posts_for_profile,
            },
        )

        try:
            database.update_audience_profile(p.id, {"postsS3Url": posts_url})
            updated.append(
                {
                    "profile_id": p.id,
                    "profile_name": p.profileName,
                    "linkedin_url": p.linkedinUrl,
                    "posts_s3_url": posts_url,
                }
            )
        except Exception as e:
            logger.error(f"Error updating posts for profile {p.id}: {e}")
            missing.append({"profile_id": p.id, "profile_name": p.profileName, "reason": "db_update_failed"})

    return {
        "audience_room_id": audience_room_id,
        "profiles_updated": len(updated),
        "profiles_missing": len(missing),
        "updated": updated,
        "missing": missing,
    }

# === GET ENDPOINTS: FETCH JSON DATA DIRECTLY FROM S3 ===
@app.get("/api/v1/audience-rooms/{audience_room_id}/description")
async def get_audience_room_description(audience_room_id: str):
    """
    Fetch and return the audience room description JSON from S3.
    
    Frontend sends audience room ID, backend fetches description.json from S3 and returns it.
    """
    ensure_db_available("audience")
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")
    
    try:
        # Verify room exists
        room = database.find_audience_room_by_id(audience_room_id)
        if not room:
            raise HTTPException(status_code=404, detail=f"Audience room {audience_room_id} not found")
        
        if not room.descriptionS3Url:
            raise HTTPException(status_code=404, detail="Description not found for this audience room")
        
        # Extract S3 key from URL
        description_key = extract_s3_key_from_url(room.descriptionS3Url)
        if not description_key:
            raise HTTPException(status_code=500, detail="Invalid S3 URL format")
        
        # Fetch JSON from S3
        description_data = fetch_json_from_s3(description_key)
        return description_data
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching description for audience room {audience_room_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch description")


@app.get("/api/v1/audience-rooms/{audience_room_id}/profiles/{profile_id}/description")
async def get_profile_description(audience_room_id: str, profile_id: str):
    """
    Fetch and return the profile description JSON from S3.
    
    Frontend sends room ID and profile ID, backend fetches profile.json from S3 and returns it.
    """
    ensure_db_available("audience")
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")
    
    try:
        # Verify profile exists and belongs to the room
        profile = database.find_audience_profile_by_id(profile_id, include_room=True)
        if not profile:
            raise HTTPException(status_code=404, detail=f"Profile {profile_id} not found")
        
        if profile.audienceRoomId != audience_room_id:
            raise HTTPException(status_code=404, detail=f"Profile {profile_id} does not belong to audience room {audience_room_id}")
        
        if not profile.profileDescriptionS3Url:
            raise HTTPException(status_code=404, detail="Profile description not found")
        
        # Extract S3 key from URL
        profile_key = extract_s3_key_from_url(profile.profileDescriptionS3Url)
        if not profile_key:
            raise HTTPException(status_code=500, detail="Invalid S3 URL format")
        
        # Fetch JSON from S3
        profile_data = fetch_json_from_s3(profile_key)
        return profile_data
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching profile description for {profile_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch profile description")


@app.get("/api/v1/audience-rooms/{audience_room_id}/profiles/{profile_id}/posts")
async def get_profile_posts(audience_room_id: str, profile_id: str):
    """
    Fetch and return the profile posts JSON from S3.
    
    Frontend sends room ID and profile ID, backend fetches posts.json from S3 and returns it.
    """
    ensure_db_available("audience")
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")
    
    try:
        # Verify profile exists and belongs to the room
        profile = database.find_audience_profile_by_id(profile_id, include_room=True)
        if not profile:
            raise HTTPException(status_code=404, detail=f"Profile {profile_id} not found")
        
        if profile.audienceRoomId != audience_room_id:
            raise HTTPException(status_code=404, detail=f"Profile {profile_id} does not belong to audience room {audience_room_id}")
        
        if not profile.postsS3Url:
            raise HTTPException(status_code=404, detail="Posts not found for this profile")
        
        # Extract S3 key from URL
        posts_key = extract_s3_key_from_url(profile.postsS3Url)
        if not posts_key:
            raise HTTPException(status_code=500, detail="Invalid S3 URL format")
        
        # Fetch JSON from S3
        posts_data = fetch_json_from_s3(posts_key)
        return posts_data
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching posts for profile {profile_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch posts")


async def _generate_summary_for_batch(
    profile_id: str,
    profile_name: str,
    profile_context: str,
    post_texts: List[str],
    total_posts: int,
    is_final: bool = True
) -> Dict[str, Any]:
    """
    Generate summary for a single batch of posts.
    Helper function for batched summary generation.
    """
    text_for_analysis = "\n\n".join(post_texts)
    
    if is_final:
        instruction = f"""Generate a comprehensive, detailed analysis:
1. A thorough 5-8 sentence summary that covers:
   - Their current role and company context (mention company stage if evident: Series A/B, startup, growth stage, etc.)
   - Main topics, themes, and subjects they frequently post about
   - Their posting style and tone (technical, thought leadership, personal reflections, etc.)
   - Key insights, opinions, expertise areas, or perspectives they share
   - Notable patterns in content (technical depth, problem-solving focus, industry commentary, etc.)
   - Engagement patterns or community involvement if evident
   - Any unique value propositions or differentiators in their content
   
   Start with "{profile_name} is currently..." or "{profile_name} has..." and write in a natural, engaging way.
   
2. Extract 4-6 key highlights/badges (similar to: "Early + Growth", "Fullstack", "Thought Leader", "Technical Expert", "Series B", "Problem Solver", "Startup Experience", etc.)

3. Identify 10-15 important keywords/phrases for highlighting"""
    else:
        instruction = f"""Analyze this batch of posts and provide:
1. A 3-4 sentence summary of the key themes, topics, and insights from these posts
2. Extract 3-4 key highlights/badges that apply to these posts
3. Identify 8-10 important keywords/phrases mentioned

This is a partial analysis that will be combined with other batches."""
    
    user_prompt = f"""Analyze the LinkedIn posts from {profile_context}.

Posts ({total_posts} in this batch):
{text_for_analysis}

{instruction}

Respond in JSON format only:
{{
    "summary": "Summary text",
    "highlights": ["Highlight 1", "Highlight 2", ...],
    "keywords": ["keyword1", "keyword2", ...]
}}"""
    
    system_message = "You are an expert at analyzing LinkedIn posts and generating professional summaries. Always respond with valid JSON only."
    
    try:
        completion = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_prompt}
            ],
            max_tokens=1000 if not is_final else 1500,
            temperature=0.3,
            response_format={"type": "json_object"}
        )
        
        result = json.loads(completion.choices[0].message.content)
        
        return {
            "summary": result.get("summary", ""),
            "highlights": result.get("highlights", []),
            "keywords": result.get("keywords", [])
        }
    except Exception as e:
        logger.error(f"Error generating batch summary for profile {profile_id}: {e}")
        return {
            "summary": None,
            "highlights": [],
            "keywords": []
        }


async def generate_profile_summary_from_posts(
    profile_id: str,
    profile_name: str,
    profile_title: Optional[str],
    profile_company: Optional[str],
    posts: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Generate summary, keywords, and highlights for a profile based on their posts.
    Uses map-reduce batching to handle large numbers of posts without hitting context limits.
    
    Args:
        profile_id: Profile ID
        profile_name: Profile name
        profile_title: Profile title/role (optional)
        profile_company: Profile company (optional)
        posts: List of post objects
    
    Returns:
        Dictionary with 'summary', 'highlights', and 'keywords' keys
    """
    if not openai_client:
        raise HTTPException(status_code=503, detail="OpenAI client not initialized. Please check OPENAI_API_KEY.")
    
    if not posts:
        return {
            "summary": None,
            "highlights": [],
            "keywords": []
        }
    
    # Extract post text content
    post_texts = []
    for post in posts:
        text = post.get("text", "")
        if text and text.strip():
            post_texts.append(text.strip())
    
    if not post_texts:
        return {
            "summary": None,
            "highlights": [],
            "keywords": []
        }
    
    # Build profile context
    if profile_title and profile_company:
        profile_context = f"{profile_name}, who is a {profile_title} at {profile_company}"
    elif profile_company:
        profile_context = f"{profile_name}, who works at {profile_company}"
    else:
        profile_context = profile_name
    
    # Batch configuration to avoid context limits
    POSTS_PER_BATCH = 100
    MAX_CHARS_PER_BATCH = 200000  # ~50k tokens, optimized for Tier 2
    
    # Split posts into batches
    batches = []
    current_batch = []
    current_chars = 0
    
    for text in post_texts:
        # Start new batch if adding this post would exceed limits
        if len(current_batch) >= POSTS_PER_BATCH or (current_chars + len(text) > MAX_CHARS_PER_BATCH and current_batch):
            batches.append(current_batch)
            current_batch = []
            current_chars = 0
        
        current_batch.append(text)
        current_chars += len(text)
    
    # Don't forget the last batch
    if current_batch:
        batches.append(current_batch)
    
    logger.info(f"Profile {profile_id}: Processing {len(post_texts)} posts in {len(batches)} batch(es)")
    
    # If only one batch, process directly (no need for map-reduce)
    if len(batches) == 1:
        return await _generate_summary_for_batch(
            profile_id, profile_name, profile_context, batches[0], len(post_texts), is_final=True
        )
    
    # Map phase: Generate intermediate summaries for each batch
    batch_summaries = []
    all_keywords = []
    all_highlights = []
    
    for idx, batch in enumerate(batches):
        logger.info(f"Profile {profile_id}: Processing batch {idx + 1}/{len(batches)} ({len(batch)} posts)")
        
        batch_result = await _generate_summary_for_batch(
            profile_id, profile_name, profile_context, batch, len(batch), is_final=False
        )
        
        if batch_result.get("summary"):
            batch_summaries.append(batch_result["summary"])
        if batch_result.get("keywords"):
            all_keywords.extend(batch_result["keywords"])
        if batch_result.get("highlights"):
            all_highlights.extend(batch_result["highlights"])
        
        # Small delay between batches to avoid rate limits
        await asyncio.sleep(0.3)
    
    # Reduce phase: Combine batch summaries into final summary
    if not batch_summaries:
        return {
            "summary": None,
            "highlights": [],
            "keywords": []
        }
    
    # Deduplicate keywords and highlights
    unique_keywords = list(dict.fromkeys(all_keywords))[:15]  # Keep top 15 unique
    unique_highlights = list(dict.fromkeys(all_highlights))[:6]  # Keep top 6 unique
    
    # Generate final combined summary from batch summaries
    combined_summaries = "\n\n".join([f"Batch {i+1}: {s}" for i, s in enumerate(batch_summaries)])
    
    combine_prompt = f"""You are combining multiple analysis summaries of LinkedIn posts from {profile_context} into ONE final comprehensive summary.

Here are the summaries from analyzing {len(post_texts)} total posts in {len(batches)} batches:

{combined_summaries}

Based on ALL these batch summaries, create ONE unified final analysis:

1. A comprehensive 5-8 sentence summary that synthesizes all the insights, covering:
   - Their current role and company context
   - Main topics and themes across ALL their posts
   - Their posting style and tone
   - Key insights, expertise areas, and perspectives
   - Notable patterns in their content
   
   Start with "{profile_name} is currently..." or "{profile_name} has..."

2. Select the 4-6 BEST highlights/badges from across all batches that best represent this person

3. Select the 10-15 MOST important keywords from across all batches

Respond in JSON format only:
{{
    "summary": "Final comprehensive 5-8 sentence summary",
    "highlights": ["Best Highlight 1", "Best Highlight 2", ...],
    "keywords": ["keyword1", "keyword2", ...]
}}"""
    
    try:
        completion = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are an expert at synthesizing multiple summaries into one comprehensive analysis. Always respond with valid JSON only."},
                {"role": "user", "content": combine_prompt}
            ],
            max_tokens=1500,
            temperature=0.3,
            response_format={"type": "json_object"}
        )
        
        result = json.loads(completion.choices[0].message.content)
        
        return {
            "summary": result.get("summary", ""),
            "highlights": result.get("highlights", unique_highlights),
            "keywords": result.get("keywords", unique_keywords)
        }
    except Exception as e:
        logger.error(f"Error combining batch summaries for profile {profile_id}: {e}")
        # Fallback: return first batch summary with collected keywords/highlights
        return {
            "summary": batch_summaries[0] if batch_summaries else None,
            "highlights": unique_highlights,
            "keywords": unique_keywords
        }


async def process_profile_summary(
    profile: Any,
    audience_room_id: str,
) -> Dict[str, Any]:
    """
    Process a single profile: fetch posts, generate summary, and update description JSON.
    
    Returns:
        Dictionary with processing results
    """
    profile_id = profile.id
    profile_name = profile.profileName
    
    try:
        # Fetch profile description JSON from S3
        if not profile.profileDescriptionS3Url:
            return {
                "profile_id": profile_id,
                "profile_name": profile_name,
                "status": "skipped",
                "reason": "no_description_url",
                "error": None
            }
        
        profile_key = extract_s3_key_from_url(profile.profileDescriptionS3Url)
        if not profile_key:
            return {
                "profile_id": profile_id,
                "profile_name": profile_name,
                "status": "error",
                "reason": "invalid_description_url",
                "error": "Invalid S3 URL format"
            }
        
        profile_data = fetch_json_from_s3(profile_key)
        
        # Extract profile info for the prompt
        # Note: profile data doesn't have a title field, so we'll use a generic description
        profile_title = None  # Could be enhanced if title is added to profile data
        profile_company = profile_data.get("current_company")
        
        # Fetch posts from S3
        if not profile.postsS3Url:
            return {
                "profile_id": profile_id,
                "profile_name": profile_name,
                "status": "skipped",
                "reason": "no_posts_url",
                "error": None
            }
        
        posts_key = extract_s3_key_from_url(profile.postsS3Url)
        if not posts_key:
            return {
                "profile_id": profile_id,
                "profile_name": profile_name,
                "status": "error",
                "reason": "invalid_posts_url",
                "error": "Invalid S3 URL format for posts"
            }
        
        posts_data = fetch_json_from_s3(posts_key)
        
        # Extract posts array
        posts = []
        if isinstance(posts_data, dict):
            posts = posts_data.get("posts", [])
            if not posts and isinstance(posts_data.get("data"), list):
                posts = posts_data["data"]
        elif isinstance(posts_data, list):
            posts = posts_data
        
        if not posts:
            return {
                "profile_id": profile_id,
                "profile_name": profile_name,
                "status": "skipped",
                "reason": "no_posts",
                "error": None
            }
        
        # Generate summary, keywords, and highlights
        summary_result = await generate_profile_summary_from_posts(
            profile_id=profile_id,
            profile_name=profile_name,
            profile_title=profile_title,
            profile_company=profile_company,
            posts=posts,
        )
        
        # Update profile description JSON
        profile_data["summary"] = summary_result["summary"]
        profile_data["highlights"] = summary_result["highlights"]
        profile_data["keywords"] = summary_result["keywords"]
        
        # Upload updated profile description back to S3
        updated_profile_url = upload_json_to_s3(profile_key, profile_data)
        
        # Update the profile record with the new URL (same key, but updated content)
        database.update_audience_profile(profile_id, {"profileDescriptionS3Url": updated_profile_url})
        
        return {
            "profile_id": profile_id,
            "profile_name": profile_name,
            "status": "success",
            "summary": summary_result["summary"][:100] + "..." if summary_result["summary"] and len(summary_result["summary"]) > 100 else summary_result["summary"],
            "highlights_count": len(summary_result["highlights"]),
            "keywords_count": len(summary_result["keywords"]),
            "error": None
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing profile {profile_id}: {e}")
        return {
            "profile_id": profile_id,
            "profile_name": profile_name,
            "status": "error",
            "reason": "processing_failed",
            "error": str(e)
        }


async def classify_post_with_groq(
    post: Dict[str, Any],
    classifier_name: str,
    classifier_prompt: str,
    classifier_description: str,
    classifier_labels: List[str],
    classifier_examples: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Classify a single post using Groq LLM.
    
    Returns:
        Dictionary with 'label' and 'score' keys
    """
    if not groq_client:
        raise HTTPException(status_code=503, detail="Groq client not initialized. Please set GROQ_API_KEY.")
    
    # Extract post content (text field from the post object)
    post_text = post.get("text", "")
    if not post_text:
        # Try alternative fields
        post_text = post.get("content", "") or post.get("description", "") or ""
    
    # Build few-shot examples string from ALL examples, with intelligent truncation
    # Set limits to prevent timeout/rate limit issues
    # Groq models typically have 32k-128k token limits (~128k-512k characters)
    # We'll use a conservative limit to ensure we don't hit issues
    MAX_EXAMPLES_LENGTH = int(os.getenv("MAX_EXAMPLES_LENGTH", "80000"))  # ~100k tokens conservative limit
    MAX_EXAMPLE_POST_LENGTH = int(os.getenv("MAX_EXAMPLE_POST_LENGTH", "2000"))  # Truncate very long posts
    
    examples_text = ""
    examples_count = 0
    examples_skipped = 0
    examples_truncated = 0
    
    if classifier_examples:
        if isinstance(classifier_examples, list):
            # Process ALL examples, but truncate if needed to prevent timeout
            for idx, example in enumerate(classifier_examples):
                if isinstance(example, dict):
                    # Handle different example formats
                    example_post = example.get("post") or example.get("text", "")
                    example_labels = example.get("labels", [])
                    example_label = example.get("label", "")
                    example_score = example.get("score", "")
                    
                    # Truncate very long example posts
                    original_post_length = len(example_post)
                    if len(example_post) > MAX_EXAMPLE_POST_LENGTH:
                        example_post = example_post[:MAX_EXAMPLE_POST_LENGTH] + "... [truncated]"
                        examples_truncated += 1
                    
                    # If labels is an array, format it properly
                    if isinstance(example_labels, list) and len(example_labels) > 0:
                        # Join labels (e.g., ["not useful", "personal"] -> "not useful, personal")
                        label_display = ", ".join(example_labels)
                    elif example_label:
                        label_display = example_label
                    else:
                        label_display = ""
                    
                    if example_post and label_display:
                        # Build this example
                        example_formatted = f"\n\nExample {idx + 1}:\nPost: {example_post}\nLabel(s): {label_display}"
                        if example_score:
                            example_formatted += f" (Score: {example_score})"
                        
                        # Check if adding this example would exceed the limit
                        if len(examples_text) + len(example_formatted) > MAX_EXAMPLES_LENGTH:
                            examples_skipped = len(classifier_examples) - idx
                            logger.warning(f"⚠️ Examples limit reached ({MAX_EXAMPLES_LENGTH} chars). Skipping {examples_skipped} remaining examples to prevent timeout.")
                            break
                        
                        examples_text += example_formatted
                        examples_count += 1
        elif isinstance(classifier_examples, dict):
            # Handle dict format - process all items with truncation
            for idx, (key, value) in enumerate(classifier_examples.items()):
                if isinstance(value, dict):
                    example_post = value.get("post") or value.get("text", "")
                    example_labels = value.get("labels", [])
                    example_label = value.get("label", key)
                    
                    # Truncate very long example posts
                    if len(example_post) > MAX_EXAMPLE_POST_LENGTH:
                        example_post = example_post[:MAX_EXAMPLE_POST_LENGTH] + "... [truncated]"
                        examples_truncated += 1
                    
                    if isinstance(example_labels, list) and len(example_labels) > 0:
                        label_display = ", ".join(example_labels)
                    elif example_label:
                        label_display = example_label
                    else:
                        label_display = key
                    
                    if example_post and label_display:
                        # Build this example
                        example_formatted = f"\n\nExample {idx + 1}:\nPost: {example_post}\nLabel(s): {label_display}"
                        
                        # Check if adding this example would exceed the limit
                        if len(examples_text) + len(example_formatted) > MAX_EXAMPLES_LENGTH:
                            remaining = len(classifier_examples) - idx
                            examples_skipped = remaining
                            logger.warning(f"⚠️ Examples limit reached ({MAX_EXAMPLES_LENGTH} chars). Skipping {examples_skipped} remaining examples to prevent timeout.")
                            break
                        
                        examples_text += example_formatted
                        examples_count += 1
    
    # Log summary
    if examples_count > 0:
        logger.info(f"📚 Included {examples_count} examples in prompt" + 
                   (f" ({examples_skipped} skipped due to length limit)" if examples_skipped > 0 else "") +
                   (f", {examples_truncated} posts truncated" if examples_truncated > 0 else ""))
    
    # Construct the SYSTEM prompt dynamically from PostClassifier.prompt
    # Use the user's prompt as the base system prompt
    system_prompt_parts = []
    
    # Start with the classifier prompt if provided (this contains all the rules and instructions)
    if classifier_prompt:
        system_prompt_parts.append(classifier_prompt)
    else:
        # Fallback if no prompt provided
        system_prompt_parts.append(f"You are a {classifier_name} classifier. Classify posts according to the available labels.")
    
    # Add available labels information
    if classifier_labels:
        labels_str = ", ".join(classifier_labels)
        system_prompt_parts.append(f"\n\nAvailable Labels: {labels_str}")
    
    # Add description if provided
    if classifier_description:
        system_prompt_parts.append(f"\n\nAdditional Context: {classifier_description}")
    
    # Add examples at the end if we have any
    if examples_text:
        system_prompt_parts.append(f"\n\nBelow are example posts with their correct classifications. Use them as ground-truth demonstrations for how to classify future posts:{examples_text}")
    
    # Combine into final system prompt
    system_prompt = "\n".join(system_prompt_parts)
    
    # Final safety check: Warn if system prompt is very long
    system_prompt_length = len(system_prompt)
    MAX_SYSTEM_PROMPT_LENGTH = int(os.getenv("MAX_SYSTEM_PROMPT_LENGTH", "100000"))  # ~125k tokens conservative limit
    
    if system_prompt_length > MAX_SYSTEM_PROMPT_LENGTH:
        logger.warning(f"⚠️ System prompt is very long ({system_prompt_length} chars). This may cause timeout issues.")
        logger.warning(f"⚠️ Consider reducing examples or prompt length. Current limit: {MAX_SYSTEM_PROMPT_LENGTH} chars")
    else:
        logger.debug(f"System prompt length: {system_prompt_length} chars (limit: {MAX_SYSTEM_PROMPT_LENGTH})")
    
    # Construct the USER prompt (post content + output format)
    labels_str = ", ".join(classifier_labels)
    labels_list_str = ", ".join([f'"{label}"' for label in classifier_labels])
    
    # User prompt includes: post content + output format requirements
    # (Classification rules are now in the system prompt)
    user_prompt_parts = []
    
    # Add the post to classify
    user_prompt_parts.append(f"## Post to Classify\n\nPost Content:\n{post_text}")
    
    # Add output format requirements
    example_scores_dict = {}
    if len(classifier_labels) > 0:
        # Distribute scores: primary gets 0.85, rest share 0.15
        remaining = 0.15 / max(1, len(classifier_labels) - 1) if len(classifier_labels) > 1 else 0.0
        for i, label in enumerate(classifier_labels):
            example_scores_dict[label] = 0.85 if i == 0 else remaining
    
    example_scores_str = ",\n    ".join([f'"{k}": {v}' for k, v in example_scores_dict.items()])
    
    user_prompt_parts.append(f"""## Required Output Format

You MUST respond with a valid JSON object with EXACTLY this structure:
{{
  "label": "<exactly ONE of the available labels>",
  "score": <number between 0.0 and 1.0>,
}}

REQUIREMENTS:
1. "label" must be EXACTLY ONE of these labels: {labels_list_str}
2. "score" must be a number between 0.0 and 1.0 representing confidence in the primary label
3. All other label scores (except the one label that will be applied) will be 0.0


Example response format:
{{
  "label": "{classifier_labels[0] if classifier_labels else 'label'}",
  "score": 0.8,
  "scores": {{
    {example_scores_str}
  }}
}}

Respond ONLY with valid JSON. No markdown, no code blocks, no explanation, just the JSON object.""")
    
    user_prompt = "\n\n".join(user_prompt_parts)
    
    # Log the full prompts for debugging
    logger.info("=" * 80)
    logger.info("CLASSIFICATION PROMPTS BEING SENT TO GROQ:")
    logger.info("=" * 80)
    logger.info(f"System Prompt (first 500 chars): {system_prompt[:500]}...")
    logger.info(f"User Prompt (first 1000 chars): {user_prompt[:1000]}...")
    logger.info(f"Full System Prompt Length: {len(system_prompt)} characters")
    logger.info(f"Full User Prompt Length: {len(user_prompt)} characters")
    logger.info(f"Classifier Name: {classifier_name}")
    logger.info(f"Labels: {classifier_labels}")
    logger.info(f"Examples Used: {bool(classifier_examples)}")
    if classifier_examples:
        logger.info(f"Examples Content: {str(classifier_examples)[:500]}")
    logger.info("=" * 80)
    
    # Get model name from environment or use default
    model_name = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    
    # Timeout configuration to prevent hanging requests
    groq_timeout = int(os.getenv("GROQ_TIMEOUT_SECONDS", "60"))  # 60 second timeout for API calls
    
    # Retry logic with exponential backoff for rate limits
    max_retries = 5
    base_delay = 2  # Start with 2 seconds
    
    for attempt in range(max_retries):
        try:
            # Call Groq API - try with json_object format first
            try:
                response = groq_client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    temperature=0.1,  # Lower temperature for more consistent classification
                    response_format={"type": "json_object"}  # Force JSON response
                )
                # Success! Break out of retry loop
                break
            except Exception as groq_error:
                error_str = str(groq_error).lower()
                error_repr = repr(groq_error).lower()
                full_error_str = str(groq_error)  # Keep original case for detailed messages
                
                # Check for token quota limit (daily limit) - these cannot be retried
                is_token_quota_limit = (
                    "tokens per day" in error_str or "tokens_per_day" in error_str or
                    "tpd" in error_str or "token quota" in error_str or
                    "token_limit" in error_str or "daily token" in error_str or
                    "type': 'tokens'" in error_str or "'code': 'rate_limit_exceeded'" in error_str
                )
                
                # Check for request rate limit errors (429, "rate limit", "too many requests")
                # These can be retried with backoff
                is_request_rate_limit = (
                    ("429" in error_str or "429" in error_repr or
                    hasattr(groq_error, 'status_code') and groq_error.status_code == 429) and
                    not is_token_quota_limit  # Make sure it's not a token quota limit
                )
                
                # Token quota limits cannot be resolved by retrying - return clear error
                if is_token_quota_limit:
                    logger.error(f"❌ Daily token quota limit exceeded. Cannot retry - quota resets daily.")
                    logger.error(f"Error details: {full_error_str}")
                    raise HTTPException(
                        status_code=429,
                        detail={
                            "error": "token_quota_exceeded",
                            "message": "Daily token quota limit reached for Groq API. This is a quota limit, not a rate limit.",
                            "type": "token_quota",
                            "suggestion": "Please wait for the daily quota to reset, or upgrade your Groq plan to increase token limits.",
                            "groq_error": str(groq_error)
                        }
                    )
                
                # Request rate limits can be retried with exponential backoff
                if is_request_rate_limit and attempt < max_retries - 1:
                    # Calculate exponential backoff delay
                    delay = base_delay * (2 ** attempt)
                    logger.warning(f"⚠️ Rate limit hit (attempt {attempt + 1}/{max_retries}). Waiting {delay} seconds before retry...")
                    await asyncio.sleep(delay)
                    continue  # Retry the API call
                
                logger.error(f"Groq API call failed with json_object format: {groq_error}")
                
                # Check if it's a model decommissioned error
                if "decommissioned" in error_str or "model_decommissioned" in error_str:
                    logger.error("⚠️ Model has been decommissioned! Trying alternative model...")
                    # Try with a different model
                    try:
                        response = groq_client.chat.completions.create(
                            model="llama-3.3-70b-versatile",  # Fallback to faster model
                            messages=[
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_prompt}
                            ],
                            temperature=0.3,
                            response_format={"type": "json_object"}
                        )
                        logger.info("✅ Successfully used fallback model: llama-3.3-70b-versatile")
                        break  # Success, exit retry loop (response is set)
                    except Exception as fallback_error:
                        fallback_error_str = str(fallback_error).lower()
                        full_fallback_error = str(fallback_error)
                        
                        # Check for token quota limit first
                        is_fallback_token_quota = (
                            "tokens per day" in fallback_error_str or "tpd" in fallback_error_str or
                            "token quota" in fallback_error_str or "type': 'tokens'" in fallback_error_str
                        )
                        
                        if is_fallback_token_quota:
                            logger.error(f"❌ Daily token quota limit exceeded on fallback model.")
                            raise HTTPException(
                                status_code=429,
                                detail={
                                    "error": "token_quota_exceeded",
                                    "message": "Daily token quota limit reached for Groq API.",
                                    "type": "token_quota",
                                    "suggestion": "Please wait for the daily quota to reset, or upgrade your Groq plan.",
                                    "groq_error": full_fallback_error
                                }
                            )
                        
                        is_fallback_rate_limit = (
                            ("429" in fallback_error_str or
                            hasattr(fallback_error, 'status_code') and fallback_error.status_code == 429) and
                            not is_fallback_token_quota
                        )
                        
                        if is_fallback_rate_limit and attempt < max_retries - 1:
                            delay = base_delay * (2 ** attempt)
                            logger.warning(f"⚠️ Rate limit on fallback model. Waiting {delay} seconds...")
                            await asyncio.sleep(delay)
                            continue
                        
                        logger.error(f"Fallback model also failed: {fallback_error}")
                        # Retry without json_object constraint
                        logger.info("Retrying without json_object constraint...")
                        try:
                            response = groq_client.chat.completions.create(
                                model="llama-3.3-70b-versatile",
                                messages=[
                                    {"role": "system", "content": system_prompt},
                                    {"role": "user", "content": user_prompt}
                                ],
                                temperature=0.1
                            )
                            break  # Success, exit retry loop (response is set)
                        except Exception as final_error:
                            final_error_str = str(final_error).lower()
                            full_final_error = str(final_error)
                            
                            # Check for token quota limit first
                            is_final_token_quota = (
                                "tokens per day" in final_error_str or "tpd" in final_error_str or
                                "token quota" in final_error_str or "type': 'tokens'" in final_error_str
                            )
                            
                            if is_final_token_quota:
                                logger.error(f"❌ Daily token quota limit exceeded on final retry.")
                                raise HTTPException(
                                    status_code=429,
                                    detail={
                                        "error": "token_quota_exceeded",
                                        "message": "Daily token quota limit reached for Groq API.",
                                        "type": "token_quota",
                                        "suggestion": "Please wait for the daily quota to reset, or upgrade your Groq plan.",
                                        "groq_error": full_final_error
                                    }
                                )
                            
                            if attempt < max_retries - 1:
                                is_final_rate_limit = (
                                    ("429" in final_error_str or
                                    "rate limit" in final_error_str or
                                    "too many requests" in final_error_str) and
                                    not is_final_token_quota
                                )
                                if is_final_rate_limit:
                                    delay = base_delay * (2 ** attempt)
                                    logger.warning(f"⚠️ Rate limit on final retry. Waiting {delay} seconds...")
                                    await asyncio.sleep(delay)
                                    continue
                            raise  # Re-raise if not rate limit or last attempt
                else:
                    # For non-decommissioned errors, check if it's a rate limit
                    # (token quota limits are already handled above and will raise HTTPException)
                    if is_request_rate_limit and attempt < max_retries - 1:
                        # Already handled above, but just in case
                        delay = base_delay * (2 ** attempt)
                        await asyncio.sleep(delay)
                        continue
                    
                    # Retry without json_object constraint
                    logger.info("Retrying without json_object constraint...")
                    try:
                        response = groq_client.chat.completions.create(
                            model=model_name,
                            messages=[
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_prompt}
                            ],
                            temperature=0.1
                        )
                        break  # Success, exit retry loop (response is set)
                    except Exception as retry_error:
                        retry_error_str = str(retry_error).lower()
                        full_retry_error = str(retry_error)
                        
                        # Check for token quota limit first
                        is_retry_token_quota = (
                            "tokens per day" in retry_error_str or "tpd" in retry_error_str or
                            "token quota" in retry_error_str or "type': 'tokens'" in retry_error_str
                        )
                        
                        if is_retry_token_quota:
                            logger.error(f"❌ Daily token quota limit exceeded on retry without json_object.")
                            raise HTTPException(
                                status_code=429,
                                detail={
                                    "error": "token_quota_exceeded",
                                    "message": "Daily token quota limit reached for Groq API.",
                                    "type": "token_quota",
                                    "suggestion": "Please wait for the daily quota to reset, or upgrade your Groq plan.",
                                    "groq_error": full_retry_error
                                }
                            )
                        
                        is_retry_rate_limit = (
                            ("429" in retry_error_str or
                            "rate limit" in retry_error_str or
                            "too many requests" in retry_error_str or
                            (hasattr(retry_error, 'status_code') and retry_error.status_code == 429)) and
                            not is_retry_token_quota
                        )
                        
                        if is_retry_rate_limit and attempt < max_retries - 1:
                            delay = base_delay * (2 ** attempt)
                            logger.warning(f"⚠️ Rate limit on retry without json_object. Waiting {delay} seconds...")
                            await asyncio.sleep(delay)
                            continue
                        
                        # If it's the last attempt or not a rate limit, raise the error
                        if attempt == max_retries - 1:
                            logger.error(f"❌ Max retries ({max_retries}) reached. Last error: {retry_error}")
                            raise
                        else:
                            raise
        except Exception as outer_error:
            # Check if it's an HTTPException (token quota or other structured errors)
            if isinstance(outer_error, HTTPException):
                raise  # Re-raise HTTPExceptions (they already have proper error messages)
            
            error_str = str(outer_error).lower()
            full_outer_error = str(outer_error)
            
            # Check for token quota limit first
            is_outer_token_quota = (
                "tokens per day" in error_str or "tpd" in error_str or
                "token quota" in error_str or "type': 'tokens'" in error_str
            )
            
            if is_outer_token_quota:
                logger.error(f"❌ Daily token quota limit exceeded (outer exception).")
                raise HTTPException(
                    status_code=429,
                    detail={
                        "error": "token_quota_exceeded",
                        "message": "Daily token quota limit reached for Groq API.",
                        "type": "token_quota",
                        "suggestion": "Please wait for the daily quota to reset, or upgrade your Groq plan.",
                        "groq_error": full_outer_error
                    }
                )
            
            # Check for request rate limit
            is_outer_rate_limit = (
                ("429" in error_str or
                "rate limit" in error_str or
                "too many requests" in error_str or
                (hasattr(outer_error, 'status_code') and outer_error.status_code == 429)) and
                not is_outer_token_quota
            )
            
            if is_outer_rate_limit and attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                logger.warning(f"⚠️ Outer exception is rate limit. Waiting {delay} seconds...")
                await asyncio.sleep(delay)
                continue
            elif attempt == max_retries - 1:
                # Last attempt failed
                logger.error(f"❌ All {max_retries} retry attempts failed. Raising error.")
                raise HTTPException(
                    status_code=429 if is_outer_rate_limit else 500,
                    detail=f"Failed to classify post after {max_retries} attempts: {str(outer_error)}"
                )
            else:
                raise
    
    # If we get here, we have a successful response
    # Parse response
    try:
        content = response.choices[0].message.content
        logger.info("=" * 80)
        logger.info("📥 GROQ RESPONSE RECEIVED")
        logger.info("=" * 80)
        logger.info(f"Response type: {type(content)}")
        logger.info(f"Response length: {len(content)} characters")
        logger.info(f"Raw Groq response (FULL):\n{content}")
        logger.info("=" * 80)
        
        # Try to extract JSON from content (in case there's extra text)
        result = None
        json_parse_attempts = []
        
        # Attempt 1: Direct JSON parse
        try:
            result = json.loads(content)
            logger.info("✅ Successfully parsed JSON response (direct parse)")
            json_parse_attempts.append("direct_parse_success")
        except json.JSONDecodeError as parse_error:
            json_parse_attempts.append(f"direct_parse_failed: {str(parse_error)}")
            logger.warning(f"Initial JSON parse failed: {parse_error}")
            
            # Attempt 2: Extract JSON from markdown code blocks
            if "```json" in content:
                json_start = content.find("```json") + 7
                json_end = content.find("```", json_start)
                if json_end != -1:
                    json_str = content[json_start:json_end].strip()
                    try:
                        result = json.loads(json_str)
                        logger.info("✅ Successfully parsed JSON from markdown code block")
                        json_parse_attempts.append("markdown_extract_success")
                    except json.JSONDecodeError:
                        json_parse_attempts.append("markdown_extract_failed")
            
            # Attempt 3: Extract JSON from any code blocks
            if result is None and "```" in content:
                json_start = content.find("```") + 3
                json_end = content.find("```", json_start)
                if json_end != -1:
                    json_str = content[json_start:json_end].strip()
                    try:
                        result = json.loads(json_str)
                        logger.info("✅ Successfully parsed JSON from code block")
                        json_parse_attempts.append("code_block_extract_success")
                    except json.JSONDecodeError:
                        json_parse_attempts.append("code_block_extract_failed")
            
            # Attempt 4: Extract JSON by finding balanced braces
            if result is None:
                start_idx = content.find('{')
                if start_idx != -1:
                    brace_count = 0
                    end_idx = start_idx
                    for i in range(start_idx, len(content)):
                        if content[i] == '{':
                            brace_count += 1
                        elif content[i] == '}':
                            brace_count -= 1
                            if brace_count == 0:
                                end_idx = i + 1
                                break
                    
                    if brace_count == 0:
                        json_str = content[start_idx:end_idx]
                        try:
                            result = json.loads(json_str)
                            logger.info("✅ Successfully extracted JSON from balanced braces")
                            json_parse_attempts.append("brace_extract_success")
                        except json.JSONDecodeError as extract_error:
                            logger.error(f"Failed to parse extracted JSON: {extract_error}")
                            json_parse_attempts.append(f"brace_extract_failed: {str(extract_error)}")
        
        if result is None:
            logger.error(f"❌ Could not parse JSON from response after all attempts")
            logger.error(f"Parse attempts: {json_parse_attempts}")
            logger.error(f"Full content: {content}")
            # Try to extract any useful information from the response
            # Maybe Groq returned text instead of JSON
            if "label" in content.lower() or "useful" in content.lower():
                logger.warning("Response contains classification keywords but isn't valid JSON")
            raise json.JSONDecodeError("Could not parse JSON from response", content, 0)
        
        logger.info(f"✅ Parsed result: {json.dumps(result, indent=2)}")
        
        # Validate and normalize response
        label = result.get("label", "")
        score = result.get("score", 0.0)
        all_scores = result.get("scores", {})
        
        # Log what we got in detail
        logger.info(f"Extracted - label: '{label}' (type: {type(label)})")
        logger.info(f"Extracted - score: {score} (type: {type(score)})")
        logger.info(f"Extracted - scores: {all_scores} (type: {type(all_scores)})")
        if isinstance(all_scores, dict):
            logger.info(f"Extracted - scores keys: {list(all_scores.keys())}")
            logger.info(f"Extracted - scores values: {list(all_scores.values())}")
        else:
            logger.warning(f"⚠️ scores is not a dict! It's: {type(all_scores)}")
        
        # Ensure label is one of the available labels
        if label not in classifier_labels:
            # Try to find closest match (case-insensitive)
            label_lower = label.lower()
            matched_label = None
            for available_label in classifier_labels:
                if available_label.lower() == label_lower:
                    matched_label = available_label
                    break
            
            if matched_label:
                label = matched_label
                logger.info(f"Matched label '{label}' (case-insensitive)")
            else:
                # Default to first label if no match
                logger.warning(f"Label '{label}' not in available labels {classifier_labels}, defaulting to '{classifier_labels[0]}'")
                label = classifier_labels[0]
        
        # Ensure score is between 0 and 1
        try:
            score = float(score)
            if score > 1.0:
                score = score / 100.0  # Convert percentage to decimal
            score = max(0.0, min(1.0, score))
        except (ValueError, TypeError):
            logger.warning(f"Invalid score value: {score}, using 0.5")
            score = 0.5  # Default score
        
        # Normalize all scores - ensure we have scores for all labels
        normalized_scores = {}
        
        # Check if we have scores dict
        if isinstance(all_scores, dict) and len(all_scores) > 0:
            logger.info(f"✅ Found scores dict with {len(all_scores)} entries")
            # We have scores, use them
            total_score = 0.0
            valid_scores_count = 0
            
            # First pass: extract and normalize all provided scores
            for available_label in classifier_labels:
                if available_label in all_scores:
                    try:
                        label_score = all_scores[available_label]
                        logger.debug(f"Processing score for '{available_label}': {label_score} (type: {type(label_score)})")
                        
                        # Convert to float
                        if isinstance(label_score, str):
                            # Try to parse string
                            label_score = float(label_score.replace('%', '').strip())
                        else:
                            label_score = float(label_score)
                        
                        # Handle percentage format (e.g., 85 instead of 0.85)
                        if label_score > 1.0:
                            label_score = label_score / 100.0
                            logger.debug(f"Converted percentage {label_score * 100}% to {label_score}")
                        
                        normalized_scores[available_label] = round(max(0.0, min(1.0, label_score)), 2)
                        total_score += normalized_scores[available_label]
                        if normalized_scores[available_label] > 0:
                            valid_scores_count += 1
                            logger.debug(f"✅ Valid score for '{available_label}': {normalized_scores[available_label]}")
                    except (ValueError, TypeError) as e:
                        # If score is invalid, use 0.0
                        logger.warning(f"⚠️ Invalid score for '{available_label}': {all_scores[available_label]} (error: {e})")
                        normalized_scores[available_label] = 0.0
                else:
                    # If label not in scores, default to 0.0
                    logger.debug(f"Label '{available_label}' not found in scores dict")
                    normalized_scores[available_label] = 0.0
            
            logger.info(f"Score extraction summary - total: {total_score}, valid: {valid_scores_count}, normalized: {normalized_scores}")
            
            # If all scores are 0 or total is 0, something went wrong - create distribution from primary label
            if total_score == 0.0 or valid_scores_count == 0:
                logger.warning(f"⚠️ All scores are 0 or invalid (total={total_score}, valid={valid_scores_count})")
                logger.warning(f"⚠️ Creating distribution from primary label score {score}")
                # Distribute: primary label gets the score, rest share (1 - score) equally
                remaining = max(0.0, 1.0 - score)
                remaining_per_label = remaining / max(1, len(classifier_labels) - 1) if len(classifier_labels) > 1 else 0.0
                
                for available_label in classifier_labels:
                    if available_label == label:
                        normalized_scores[available_label] = round(score, 2)
                    else:
                        normalized_scores[available_label] = round(remaining_per_label, 2)
                logger.info(f"Created distribution: {normalized_scores}")
            elif total_score > 1.0:
                # Normalize if sum > 1.0
                logger.info(f"Normalizing scores (sum={total_score})")
                for available_label in classifier_labels:
                    normalized_scores[available_label] = round(normalized_scores[available_label] / total_score, 2)
            elif total_score < 1.0 and total_score > 0:
                # Distribute remaining probability to missing/zero labels
                remaining = 1.0 - total_score
                missing_labels = [l for l in classifier_labels if normalized_scores[l] == 0.0]
                if missing_labels:
                    per_missing = remaining / len(missing_labels)
                    for missing_label in missing_labels:
                        normalized_scores[missing_label] = round(per_missing, 2)
                else:
                    # All labels have scores, normalize to sum to 1.0
                    for available_label in classifier_labels:
                        normalized_scores[available_label] = round(normalized_scores[available_label] / total_score, 2)
        else:
            # If scores not provided or invalid, create distribution from primary label
            logger.warning(f"⚠️ No scores dict provided or empty (all_scores type: {type(all_scores)}, value: {all_scores})")
            logger.warning(f"⚠️ Creating distribution from primary label '{label}' with score {score}")
            
            # Try to use the score from result if available, otherwise use 0.8 as default confidence
            if score <= 0 or score == 0.5:
                # If score is default or invalid, use a reasonable default
                score = 0.8
                logger.info(f"Using default confidence score of 0.8 for primary label")
            
            # Distribute: primary label gets the score, rest share (1 - score) equally
            remaining = max(0.0, 1.0 - score)
            remaining_per_label = remaining / max(1, len(classifier_labels) - 1) if len(classifier_labels) > 1 else 0.0
            
            for available_label in classifier_labels:
                if available_label == label:
                    normalized_scores[available_label] = round(score, 2)
                else:
                    normalized_scores[available_label] = round(remaining_per_label, 2)
            
            logger.info(f"Created distribution from label: {normalized_scores}")
        
        logger.info(f"Final normalized scores: {normalized_scores}")
        
        return {
            "label": label,
            "score": round(score, 2),
            "allScores": normalized_scores
        }
    except json.JSONDecodeError as e:
        logger.error("=" * 80)
        logger.error("❌ JSON DECODE ERROR - RETURNING DEFAULTS")
        logger.error("=" * 80)
        logger.error(f"Error: {e}")
        logger.error(f"Response content (FULL): {content if 'content' in locals() else 'N/A'}")
        logger.error(f"Response length: {len(content) if 'content' in locals() else 0}")
        logger.error(f"Response type: {type(content) if 'content' in locals() else 'N/A'}")
        if 'content' in locals() and content:
            logger.error(f"First 1000 chars: {content[:1000]}")
            logger.error(f"Last 500 chars: {content[-500:]}")
        logger.error("=" * 80)
        # Return default classification with all scores set to 0 except the first
        default_label = classifier_labels[0] if classifier_labels else "Unknown"
        default_scores = {label: 0.0 for label in classifier_labels}
        if default_label in default_scores:
            default_scores[default_label] = 0.5
        logger.error(f"⚠️ Returning default scores: {default_scores}")
        return {
            "label": default_label,
            "score": 0.5,
            "allScores": default_scores
        }
    except Exception as e:
        logger.error("=" * 80)
        logger.error("❌ EXCEPTION IN CLASSIFICATION - RETURNING DEFAULTS")
        logger.error("=" * 80)
        logger.error(f"Error type: {type(e).__name__}")
        logger.error(f"Error message: {str(e)}")
        if 'content' in locals():
            logger.error(f"Response content was: {content}")
        import traceback
        logger.error(f"Traceback:\n{traceback.format_exc()}")
        logger.error("=" * 80)
        # Return defaults instead of raising
        default_label = classifier_labels[0] if classifier_labels else "Unknown"
        default_scores = {label: 0.0 for label in classifier_labels}
        if default_label in default_scores:
            default_scores[default_label] = 0.5
        logger.error(f"⚠️ Returning default scores due to exception: {default_scores}")
        return {
            "label": default_label,
            "score": 0.5,
            "allScores": default_scores
        }


def _match_classifications_to_posts(classifications: List[Dict], expected_count: int, classifier_labels: List[str]) -> List[Dict]:
    """
    Intelligently match classifications to posts when count doesn't match.
    Uses post_id field if available, otherwise truncates/pads.
    """
    # Try to use post_id for matching if available
    has_post_ids = all(isinstance(c.get("post_id"), int) for c in classifications if isinstance(c, dict))
    
    if has_post_ids:
        # Create a mapping by post_id
        id_to_classification = {}
        for c in classifications:
            if isinstance(c, dict):
                post_id = c.get("post_id")
                if isinstance(post_id, int) and 1 <= post_id <= expected_count:
                    id_to_classification[post_id] = c
        
        # Build result array using post_ids
        matched_results = []
        for i in range(1, expected_count + 1):
            if i in id_to_classification:
                matched_results.append(id_to_classification[i])
            else:
                # Fill with default for missing post_id
                default_label = classifier_labels[0] if classifier_labels else "Unknown"
                default_scores = {label: 0.0 for label in classifier_labels}
                if default_label in default_scores:
                    default_scores[default_label] = 0.5
                matched_results.append({
                    "label": default_label,
                    "score": 0.5,
                    "scores": default_scores
                })
        
        logger.info(f"✅ Matched {len(id_to_classification)} classifications by post_id, filled {expected_count - len(id_to_classification)} with defaults")
        return matched_results
    
    # Fallback: truncate or pad
    if len(classifications) > expected_count:
        logger.info(f"✅ Truncating {len(classifications)} classifications to {expected_count}")
        return classifications[:expected_count]
    else:
        # Pad with defaults
        result = list(classifications)
        default_label = classifier_labels[0] if classifier_labels else "Unknown"
        while len(result) < expected_count:
            default_scores = {label: 0.0 for label in classifier_labels}
            if default_label in default_scores:
                default_scores[default_label] = 0.5
            result.append({
                "label": default_label,
                "score": 0.5,
                "scores": default_scores
            })
        logger.info(f"✅ Padded {len(classifications)} classifications to {expected_count} with defaults")
        return result


async def classify_multiple_posts_single_call(
    posts_texts: List[str],
    classifier_name: str,
    classifier_prompt: str,
    classifier_description: str,
    classifier_labels: List[str],
    classifier_examples: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Classify multiple posts in a SINGLE API call to Groq.
    More efficient than separate calls - sends all posts together.
    
    Args:
        posts_texts: List of post text strings to classify (extracted text only)
        classifier_name: Name of the classifier
        classifier_prompt: System prompt for the classifier
        classifier_description: Rules/description for classification
        classifier_labels: Available labels
        classifier_examples: Few-shot examples
    
    Returns:
        List of classification results (dicts with 'label', 'score', and 'allScores')
    """
    if not groq_client:
        raise HTTPException(status_code=503, detail="Groq client not initialized. Please set GROQ_API_KEY.")
    
    if not posts_texts:
        return []
    
    # Build few-shot examples string (same as classify_post_with_groq)
    MAX_EXAMPLES_LENGTH = int(os.getenv("MAX_EXAMPLES_LENGTH", "80000"))
    MAX_EXAMPLE_POST_LENGTH = int(os.getenv("MAX_EXAMPLE_POST_LENGTH", "2000"))
    
    examples_text = ""
    examples_count = 0
    examples_skipped = 0
    examples_truncated = 0
    
    if classifier_examples:
        if isinstance(classifier_examples, list):
            for idx, example in enumerate(classifier_examples):
                if isinstance(example, dict):
                    example_post = example.get("post") or example.get("text", "")
                    example_labels = example.get("labels", [])
                    example_label = example.get("label", "")
                    example_score = example.get("score", "")
                    
                    if len(example_post) > MAX_EXAMPLE_POST_LENGTH:
                        example_post = example_post[:MAX_EXAMPLE_POST_LENGTH] + "... [truncated]"
                        examples_truncated += 1
                    
                    if isinstance(example_labels, list) and len(example_labels) > 0:
                        label_display = ", ".join(example_labels)
                    elif example_label:
                        label_display = example_label
                    else:
                        label_display = ""
                    
                    if example_post and label_display:
                        example_formatted = f"\n\nExample {idx + 1}:\nPost: {example_post}\nLabel(s): {label_display}"
                        if example_score:
                            example_formatted += f" (Score: {example_score})"
                        
                        if len(examples_text) + len(example_formatted) > MAX_EXAMPLES_LENGTH:
                            examples_skipped = len(classifier_examples) - idx
                            logger.warning(f"⚠️ Examples limit reached ({MAX_EXAMPLES_LENGTH} chars). Skipping {examples_skipped} remaining examples.")
                            break
                        
                        examples_text += example_formatted
                        examples_count += 1
        elif isinstance(classifier_examples, dict):
            for idx, (key, value) in enumerate(classifier_examples.items()):
                if isinstance(value, dict):
                    example_post = value.get("post") or value.get("text", "")
                    example_labels = value.get("labels", [])
                    example_label = value.get("label", key)
                    
                    if len(example_post) > MAX_EXAMPLE_POST_LENGTH:
                        example_post = example_post[:MAX_EXAMPLE_POST_LENGTH] + "... [truncated]"
                        examples_truncated += 1
                    
                    if isinstance(example_labels, list) and len(example_labels) > 0:
                        label_display = ", ".join(example_labels)
                    elif example_label:
                        label_display = example_label
                    else:
                        label_display = key
                    
                    if example_post and label_display:
                        example_formatted = f"\n\nExample {idx + 1}:\nPost: {example_post}\nLabel(s): {label_display}"
                        
                        if len(examples_text) + len(example_formatted) > MAX_EXAMPLES_LENGTH:
                            remaining = len(classifier_examples) - idx
                            examples_skipped = remaining
                            logger.warning(f"⚠️ Examples limit reached ({MAX_EXAMPLES_LENGTH} chars). Skipping {remaining} remaining examples.")
                            break
                        
                        examples_text += example_formatted
                        examples_count += 1
    
    # Build system prompt (same as classify_post_with_groq)
    system_prompt_parts = []
    
    if classifier_prompt:
        system_prompt_parts.append(classifier_prompt)
    else:
        system_prompt_parts.append(f"You are a {classifier_name} classifier. Classify posts according to the available labels.")
    
    if classifier_labels:
        labels_str = ", ".join(classifier_labels)
        system_prompt_parts.append(f"\n\nAvailable Labels: {labels_str}")
    
    if classifier_description:
        system_prompt_parts.append(f"\n\nAdditional Context: {classifier_description}")
    
    if examples_text:
        system_prompt_parts.append(f"\n\nBelow are example posts with their correct classifications. Use them as ground-truth demonstrations for how to classify future posts:{examples_text}")
    
    system_prompt = "\n".join(system_prompt_parts)
    
    # Build user prompt with ALL posts
    labels_list_str = ", ".join([f'"{label}"' for label in classifier_labels])
    num_posts = len(posts_texts)
    
    # Format all posts for classification with UNIQUE delimiters that won't appear in content
    # Using distinctive markers that are extremely unlikely to appear in LinkedIn post content
    posts_section = ""
    for idx, post_text in enumerate(posts_texts, 1):
        # Use unique delimiters with special characters that won't appear in normal post content
        posts_section += f"\n\n<<<POST_ID_{idx}_START>>>\n{post_text}\n<<<POST_ID_{idx}_END>>>"
    
    user_prompt = f"""## Classification Task

IMPORTANT: You are classifying EXACTLY {num_posts} posts. Count them carefully.
Each post is clearly delimited with <<<POST_ID_X_START>>> and <<<POST_ID_X_END>>> markers.
DO NOT split a single post into multiple classifications. Each delimited block = 1 post = 1 classification.

## Posts to Classify (Total: {num_posts} posts)
{posts_section}

## Required Output Format

Respond with a JSON object containing EXACTLY {num_posts} classifications (one per post):
{{
  "classifications": [
    {{"post_id": 1, "label": "<label>", "score": <0.0-1.0> }},
    {{"post_id": 2, "label": "<label>", "score": <0.0-1.0> }},
    ... (continue for ALL {num_posts} posts)
    {{"post_id": {num_posts}, "label": "<label>", "score": <0.0-1.0> }}
  ]
}}

## STRICT REQUIREMENTS:
1. "classifications" array must have EXACTLY {num_posts} objects - no more, no less
2. Include "post_id" (1 to {num_posts}) in each object to match the post number
3. "label" must be exactly one of these labels: {labels_list_str}
4. "score" is confidence (0.0-1.0) for the primary label
5. Order MUST be: post_id 1 first, post_id {num_posts} last

⚠️ CRITICAL: Output EXACTLY {num_posts} classification objects. Double-check your count before responding.

Respond ONLY with valid JSON."""
    
    # Get model name
    model_name = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    
    # Retry logic with exponential backoff for rate limits AND count mismatches
    max_retries = 5
    max_count_mismatch_retries = 2  # Additional retries specifically for count mismatch
    base_delay = 2
    
    classifications = None
    last_count_received = 0
    
    for attempt in range(max_retries):
        count_mismatch_retry = 0
        
        while count_mismatch_retry <= max_count_mismatch_retries:
            try:
                try:
                    # Adjust temperature slightly on count mismatch retries to get different results
                    temp = 0.3 if count_mismatch_retry == 0 else 0.2 + (count_mismatch_retry * 0.1)
                    
                    response = groq_client.chat.completions.create(
                        model=model_name,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt}
                        ],
                        temperature=temp,
                        response_format={"type": "json_object"}
                    )
                    
                    # Parse response immediately to check count
                    content = response.choices[0].message.content
                    
                    # Try to extract JSON
                    result = None
                    try:
                        result = json.loads(content)
                    except json.JSONDecodeError:
                        # Try extracting from markdown code blocks
                        if "```json" in content:
                            json_start = content.find("```json") + 7
                            json_end = content.find("```", json_start)
                            if json_end != -1:
                                result = json.loads(content[json_start:json_end].strip())
                        elif "```" in content:
                            json_start = content.find("```") + 3
                            json_end = content.find("```", json_start)
                            if json_end != -1:
                                result = json.loads(content[json_start:json_end].strip())
                        else:
                            # Try balanced braces
                            start_idx = content.find('{')
                            if start_idx != -1:
                                brace_count = 0
                                end_idx = start_idx
                                for i in range(start_idx, len(content)):
                                    if content[i] == '{':
                                        brace_count += 1
                                    elif content[i] == '}':
                                        brace_count -= 1
                                        if brace_count == 0:
                                            end_idx = i + 1
                                            break
                                if brace_count == 0:
                                    result = json.loads(content[start_idx:end_idx])
                    
                    if not result:
                        raise json.JSONDecodeError("Could not parse JSON from response", content, 0)
                    
                    # Extract classifications array
                    classifications = result.get("classifications", [])
                    
                    if not isinstance(classifications, list):
                        raise ValueError(f"Expected classifications array, got: {type(classifications)}")
                    
                    last_count_received = len(classifications)
                    
                    # Check count - if mismatch, retry with count mismatch logic
                    if len(classifications) != num_posts:
                        if count_mismatch_retry < max_count_mismatch_retries:
                            logger.warning(f"⚠️ Count mismatch: expected {num_posts}, got {len(classifications)}. Retrying ({count_mismatch_retry + 1}/{max_count_mismatch_retries})...")
                            count_mismatch_retry += 1
                            await asyncio.sleep(0.5)  # Brief pause before retry
                            continue
                        else:
                            # Exhausted count mismatch retries - use intelligent matching
                            logger.warning(f"⚠️ Count mismatch persists after {max_count_mismatch_retries} retries. Expected {num_posts}, got {len(classifications)}. Using intelligent matching...")
                            classifications = _match_classifications_to_posts(classifications, num_posts, classifier_labels)
                    
                    # Success - we have the right count or have handled the mismatch
                    break
                    
                except json.JSONDecodeError as json_err:
                    logger.error(f"JSON parse error: {json_err}")
                    if count_mismatch_retry < max_count_mismatch_retries:
                        count_mismatch_retry += 1
                        await asyncio.sleep(0.5)
                        continue
                    raise
                    
                except Exception as groq_error:
                    error_str = str(groq_error).lower()
                    error_repr = repr(groq_error).lower()
                    full_error_str = str(groq_error)
                    
                    # Check for token quota limit (daily limit) - cannot retry
                    is_token_quota_limit = (
                        "tokens per day" in error_str or "tpd" in error_str or
                        "token quota" in error_str or "type': 'tokens'" in error_str or
                        "'code': 'rate_limit_exceeded'" in error_str
                    )
                    
                    if is_token_quota_limit:
                        logger.error(f"❌ Daily token quota limit exceeded. Cannot retry - quota resets daily.")
                        raise HTTPException(
                            status_code=429,
                            detail={
                                "error": "token_quota_exceeded",
                                "message": "Daily token quota limit reached for Groq API. This is a quota limit, not a rate limit.",
                                "type": "token_quota",
                                "suggestion": "Please wait for the daily quota to reset, or upgrade your Groq plan to increase token limits.",
                                "groq_error": full_error_str
                            }
                        )
                    
                    # Check for request rate limit - can retry
                    is_request_rate_limit = (
                        ("429" in error_str or "429" in error_repr or
                        hasattr(groq_error, 'status_code') and groq_error.status_code == 429) and
                        not is_token_quota_limit
                    )
                    
                    if is_request_rate_limit:
                        raise  # Let outer loop handle rate limit retry
                    
                    # For other errors, raise to outer loop
                    logger.error(f"Groq API call failed: {groq_error}")
                    raise
            
            except HTTPException:
                raise
            except Exception as inner_error:
                if count_mismatch_retry < max_count_mismatch_retries:
                    count_mismatch_retry += 1
                    await asyncio.sleep(0.5)
                    continue
                raise
        
        # If we got here with valid classifications, break outer loop
        if classifications is not None and len(classifications) > 0:
            break
            
        # Handle outer retry logic for rate limits
        if attempt < max_retries - 1:
            delay = base_delay * (2 ** attempt)
            logger.warning(f"⚠️ Retry attempt {attempt + 1}/{max_retries}. Waiting {delay} seconds...")
            await asyncio.sleep(delay)
            continue
        else:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to classify posts after {max_retries} attempts"
            )
    
    # Final validation - ensure we have classifications
    if classifications is None:
        raise HTTPException(status_code=500, detail="Failed to get classifications from API")
    
    # Normalize each classification (same logic as classify_post_with_groq)
    normalized_results = []
    for idx, classification in enumerate(classifications):
        label = classification.get("label", "")
        score = classification.get("score", 0.0)
        all_scores = classification.get("scores", {})
        
        # Validate and normalize label
        if label not in classifier_labels:
            label_lower = label.lower()
            matched_label = None
            for available_label in classifier_labels:
                if available_label.lower() == label_lower:
                    matched_label = available_label
                    break
            label = matched_label if matched_label else (classifier_labels[0] if classifier_labels else "Unknown")
        
        # Normalize score
        try:
            score = float(score)
            if score > 1.0:
                score = score / 100.0
            score = max(0.0, min(1.0, score))
        except (ValueError, TypeError):
            score = 0.5
        
        # Normalize all scores
        normalized_scores = {}
        if isinstance(all_scores, dict) and len(all_scores) > 0:
            total_score = 0.0
            for available_label in classifier_labels:
                if available_label in all_scores:
                    try:
                        label_score = float(all_scores[available_label])
                        if label_score > 1.0:
                            label_score = label_score / 100.0
                        normalized_scores[available_label] = round(max(0.0, min(1.0, label_score)), 2)
                        total_score += normalized_scores[available_label]
                    except (ValueError, TypeError):
                        normalized_scores[available_label] = 0.0
                else:
                    normalized_scores[available_label] = 0.0
            
            if total_score > 1.0:
                for available_label in classifier_labels:
                    normalized_scores[available_label] = round(normalized_scores[available_label] / total_score, 2)
            elif total_score < 1.0 and total_score > 0:
                remaining = 1.0 - total_score
                missing_labels = [l for l in classifier_labels if normalized_scores[l] == 0.0]
                if missing_labels:
                    per_missing = remaining / len(missing_labels)
                    for missing_label in missing_labels:
                        normalized_scores[missing_label] = round(per_missing, 2)
                else:
                    for available_label in classifier_labels:
                        normalized_scores[available_label] = round(normalized_scores[available_label] / total_score, 2)
            elif total_score == 0:
                remaining = max(0.0, 1.0 - score)
                remaining_per_label = remaining / max(1, len(classifier_labels) - 1) if len(classifier_labels) > 1 else 0.0
                for available_label in classifier_labels:
                    if available_label == label:
                        normalized_scores[available_label] = round(score, 2)
                    else:
                        normalized_scores[available_label] = round(remaining_per_label, 2)
        else:
            remaining = max(0.0, 1.0 - score)
            remaining_per_label = remaining / max(1, len(classifier_labels) - 1) if len(classifier_labels) > 1 else 0.0
            for available_label in classifier_labels:
                if available_label == label:
                    normalized_scores[available_label] = round(score, 2)
                else:
                    normalized_scores[available_label] = round(remaining_per_label, 2)
        
        normalized_results.append({
            "label": label,
            "score": round(score, 2),
            "allScores": normalized_scores
        })
    
    # Ensure we have the same number of results as posts
    while len(normalized_results) < num_posts:
        default_label = classifier_labels[0] if classifier_labels else "Unknown"
        default_scores = {label: 0.0 for label in classifier_labels}
        if default_label in default_scores:
            default_scores[default_label] = 0.5
        normalized_results.append({
            "label": default_label,
            "score": 0.5,
            "allScores": default_scores
        })
    
    return normalized_results[:num_posts]  # Return only what we need


async def classify_posts_batch(
    posts: List[Dict[str, Any]],
    classifier_name: str,
    classifier_prompt: str,
    classifier_description: str,
    classifier_labels: List[str],
    classifier_examples: Optional[Dict[str, Any]] = None,
    batch_size: int = 20,  # Process 20 posts together in one API call
) -> List[Dict[str, Any]]:
    """
    Classify multiple posts in batches using Groq.
    Each batch sends multiple posts in a SINGLE API call (much more efficient).
    
    Args:
        posts: List of post objects to classify
        classifier_name: Name of the classifier
        classifier_prompt: System prompt for the classifier
        classifier_description: Rules/description for classification
        classifier_labels: Available labels
        classifier_examples: Few-shot examples
        batch_size: Number of posts to send together in one API call (default: 20)
    
    Returns:
        List of classification results (dicts with 'label', 'score', and 'allScores')
    """
    results = []
    
    # Extract text from all posts (only send text field, not full objects)
    posts_texts = []
    for post in posts:
        post_text = post.get("text", "")
        if not post_text:
            # Try alternative fields
            post_text = post.get("content", "") or post.get("description", "") or ""
        posts_texts.append(post_text)
    
    # Process in batches - each batch sends multiple posts in ONE API call
    for i in range(0, len(posts_texts), batch_size):
        batch_texts = posts_texts[i:i + batch_size]
        batch_num = (i // batch_size) + 1
        total_batches = (len(posts_texts) + batch_size - 1) // batch_size
        
        logger.info(f"Processing batch {batch_num}/{total_batches} ({len(batch_texts)} posts in single API call)")
        
        try:
            # Classify all posts in this batch with ONE API call
            batch_results = await classify_multiple_posts_single_call(
                posts_texts=batch_texts,
                classifier_name=classifier_name,
                classifier_prompt=classifier_prompt,
                classifier_description=classifier_description,
                classifier_labels=classifier_labels,
                classifier_examples=classifier_examples,
            )
            
            # Add results
            results.extend(batch_results)
            
        except Exception as e:
            logger.error(f"Error classifying batch {batch_num}: {e}")
            # Use default classification for all posts in this batch
            default_label = classifier_labels[0] if classifier_labels else "Unknown"
            for _ in batch_texts:
                default_scores = {label: 0.0 for label in classifier_labels}
                if default_label in default_scores:
                    default_scores[default_label] = 0.5
                results.append({
                    "label": default_label,
                    "score": 0.5,
                    "allScores": default_scores
                })
        
        # Add delay between batches to avoid rate limits (except after the last batch)
        if i + batch_size < len(posts_texts):
            delay_between_batches = 1.0  # 1 second delay between batches
            logger.info(f"Waiting {delay_between_batches} seconds before next batch to avoid rate limits...")
            await asyncio.sleep(delay_between_batches)
    
    return results


# === CLASSIFIER ENDPOINT ===
@app.post("/api/classifier/run")
async def run_classifier(payload: RunClassifierRequest):
    """
    Run a classifier on all posts in an audience room.
    
    Flow:
    1. Fetch Classifier details from audience database
    2. Fetch AudienceRoom and all associated Profiles
    3. For each Profile, download posts from S3
    4. Classify each post using Groq LLM
    5. Add labels to posts and optionally upload back to S3
    """
    ensure_db_available("audience")
    if not groq_client:
        raise HTTPException(status_code=503, detail="Groq client not initialized. Please set GROQ_API_KEY.")
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")
    
    try:
        # 1. Fetch Classifier details using psycopg2
        classifier = database.find_post_classifier_by_id(payload.classifierId)
        if not classifier:
            raise HTTPException(status_code=404, detail=f"Classifier {payload.classifierId} not found")
        
        # Extract classifier fields
        classifier_name = classifier.name
        classifier_prompt = classifier.prompt or ""
        classifier_description = classifier.description or ""
        
        # Handle labels (Prisma Json field - could be list, dict, or string representation)
        classifier_labels = []
        try:
            labels_raw = classifier.labels
            # If it's already a list, use it directly
            if isinstance(labels_raw, list):
                classifier_labels = labels_raw
            # If it's a string, try to parse it
            elif isinstance(labels_raw, str):
                try:
                    parsed = json.loads(labels_raw)
                    if isinstance(parsed, list):
                        classifier_labels = parsed
                    else:
                        classifier_labels = [labels_raw]
                except (json.JSONDecodeError, TypeError):
                    # If parsing fails, treat as single label
                    classifier_labels = [labels_raw]
            # If it's a dict (unlikely but handle it)
            elif isinstance(labels_raw, dict):
                # Try to extract list from dict, or convert dict keys/values
                if "labels" in labels_raw and isinstance(labels_raw["labels"], list):
                    classifier_labels = labels_raw["labels"]
                else:
                    classifier_labels = list(labels_raw.keys()) if labels_raw else []
            else:
                # Try to convert to list if it's iterable
                try:
                    classifier_labels = list(labels_raw) if labels_raw else []
                except (TypeError, ValueError):
                    classifier_labels = []
        except Exception as e:
            logger.warning(f"Error parsing classifier labels: {e}")
            classifier_labels = []
        
        # Ensure all labels are strings
        classifier_labels = [str(label) for label in classifier_labels if label]
        
        if not classifier_labels:
            raise HTTPException(status_code=400, detail="Classifier has no labels defined")
        
        # Handle examples (JSON field)
        classifier_examples = None
        if classifier.examples:
            try:
                examples_raw = classifier.examples
                if isinstance(examples_raw, dict):
                    classifier_examples = examples_raw
                elif isinstance(examples_raw, str):
                    try:
                        classifier_examples = json.loads(examples_raw)
                    except json.JSONDecodeError:
                        classifier_examples = None
                elif isinstance(examples_raw, list):
                    classifier_examples = examples_raw
                else:
                    classifier_examples = None
            except Exception as e:
                logger.warning(f"Error parsing classifier examples: {e}")
                classifier_examples = None
        
        # 2. Fetch AudienceRoom and Profiles
        audience_room = database.find_audience_room_by_id(payload.audienceRoomId, include_profiles=True)
        if not audience_room:
            raise HTTPException(status_code=404, detail=f"Audience room {payload.audienceRoomId} not found")
        
        profiles = audience_room.profiles
        if not profiles:
            raise HTTPException(status_code=404, detail=f"No profiles found in audience room {payload.audienceRoomId}")
        
        # 3. Process each profile's posts
        processed_profiles = []
        total_posts_classified = 0
        
        for profile in profiles:
            profile_id = profile.id
            profile_name = profile.profileName
            
            # Skip if no posts URL
            if not profile.postsS3Url:
                logger.warning(f"Profile {profile_id} ({profile_name}) has no posts URL, skipping")
                processed_profiles.append({
                    "profile_id": profile_id,
                    "profile_name": profile_name,
                    "status": "skipped",
                    "reason": "no_posts_url",
                    "posts_classified": 0
                })
                continue
            
            try:
                # Extract S3 key and fetch posts
                posts_key = extract_s3_key_from_url(profile.postsS3Url)
                if not posts_key:
                    logger.error(f"Invalid S3 URL format for profile {profile_id}: {profile.postsS3Url}")
                    processed_profiles.append({
                        "profile_id": profile_id,
                        "profile_name": profile_name,
                        "status": "error",
                        "reason": "invalid_s3_url",
                        "posts_classified": 0
                    })
                    continue
                
                # Fetch posts JSON from S3
                posts_data = fetch_json_from_s3(posts_key)
                
                # Extract posts array (could be in different formats)
                posts = []
                if isinstance(posts_data, dict):
                    posts = posts_data.get("posts", [])
                    if not posts and isinstance(posts_data.get("data"), list):
                        posts = posts_data["data"]
                elif isinstance(posts_data, list):
                    posts = posts_data
                
                if not posts:
                    logger.warning(f"No posts found for profile {profile_id}")
                    processed_profiles.append({
                        "profile_id": profile_id,
                        "profile_name": profile_name,
                        "status": "skipped",
                        "reason": "no_posts",
                        "posts_classified": 0
                    })
                    continue
                
                # 4. Classify all posts
                logger.info(f"Classifying {len(posts)} posts for profile {profile_id}")
                classification_results = await classify_posts_batch(
                    posts=posts,
                    classifier_name=classifier_name,
                    classifier_prompt=classifier_prompt,
                    classifier_description=classifier_description,
                    classifier_labels=classifier_labels,
                    classifier_examples=classifier_examples,
                    batch_size=20,  # Process 20 posts together in one API call
                )
                
                # 5. Add labels to each post
                for idx, post in enumerate(posts):
                    if idx < len(classification_results):
                        classification = classification_results[idx]
                        # Create labels object with all scores
                        labels_obj = classification.get("allScores", {})
                        # Add classifierId to the labels object
                        labels_obj["classifierId"] = payload.classifierId
                        post["labels"] = labels_obj
                
                # Update the posts data structure
                if isinstance(posts_data, dict):
                    posts_data["posts"] = posts
                else:
                    posts_data = posts
                
                # 6. Upload updated posts back to S3
                updated_posts_url = upload_json_to_s3(posts_key, posts_data)
                
                # Update profile record with new posts URL (same key, but updated content)
                database.update_audience_profile(profile_id, {"postsS3Url": updated_posts_url})
                
                total_posts_classified += len(posts)
                processed_profiles.append({
                    "profile_id": profile_id,
                    "profile_name": profile_name,
                    "status": "success",
                    "posts_classified": len(posts),
                    "updated_posts_url": updated_posts_url
                })
                
            except Exception as e:
                logger.error(f"Error processing profile {profile_id}: {e}")
                processed_profiles.append({
                    "profile_id": profile_id,
                    "profile_name": profile_name,
                    "status": "error",
                    "reason": str(e),
                    "posts_classified": 0
                })
        
        return {
            "classifier_id": payload.classifierId,
            "classifier_name": classifier_name,
            "audience_room_id": payload.audienceRoomId,
            "total_profiles_processed": len(profiles),
            "total_posts_classified": total_posts_classified,
            "profiles": processed_profiles
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error running classifier: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to run classifier: {str(e)}")


# === TEST ENDPOINT: Classify Single Post (for debugging) ===
@app.post("/api/classifier/test-single")
async def test_classify_single_post(
    classifier_id: str = Body(..., description="ID of the classifier"),
    post_text: str = Body(..., description="Post text to classify")
):
    """
    Test endpoint to classify a single post and return full debug information.
    This helps debug why classification is returning defaults.
    """
    ensure_db_available("audience")
    if not groq_client:
        raise HTTPException(status_code=503, detail="Groq client not initialized.")
    
    try:
        # Fetch classifier
        classifier = database.find_post_classifier_by_id(classifier_id)
        if not classifier:
            raise HTTPException(status_code=404, detail=f"Classifier {classifier_id} not found")
        
        # Parse classifier data (same as run_classifier)
        classifier_name = classifier.name or ''
        classifier_prompt = classifier.prompt or ""
        classifier_description = classifier.description or ""
        
        # Parse labels
        labels_raw = classifier.labels or []
        if isinstance(labels_raw, str):
            try:
                classifier_labels = json.loads(labels_raw)
            except:
                classifier_labels = [labels_raw]
        elif isinstance(labels_raw, list):
            classifier_labels = labels_raw
        elif isinstance(labels_raw, dict):
            classifier_labels = list(labels_raw.keys()) if labels_raw else []
        else:
            classifier_labels = []
        classifier_labels = [str(l) for l in classifier_labels if l]
        
        # Parse examples
        examples_raw = classifier.examples
        classifier_examples = None
        if examples_raw:
            if isinstance(examples_raw, dict):
                classifier_examples = examples_raw
            elif isinstance(examples_raw, str):
                try:
                    classifier_examples = json.loads(examples_raw)
                except:
                    classifier_examples = None
            elif isinstance(examples_raw, list):
                classifier_examples = examples_raw
        
        # Build the prompts (same as classify_post_with_groq) - using dynamic prompt building with safeguards
        # Set limits to prevent timeout/rate limit issues
        MAX_EXAMPLES_LENGTH = int(os.getenv("MAX_EXAMPLES_LENGTH", "80000"))
        MAX_EXAMPLE_POST_LENGTH = int(os.getenv("MAX_EXAMPLE_POST_LENGTH", "2000"))
        
        examples_text = ""
        examples_count = 0
        examples_skipped = 0
        examples_truncated = 0
        
        if classifier_examples:
            if isinstance(classifier_examples, list):
                # Process ALL examples, but truncate if needed to prevent timeout
                for idx, example in enumerate(classifier_examples):
                    if isinstance(example, dict):
                        example_post = example.get("post") or example.get("text", "")
                        example_labels = example.get("labels", [])
                        example_label = example.get("label", "")
                        example_score = example.get("score", "")
                        
                        # Truncate very long example posts
                        if len(example_post) > MAX_EXAMPLE_POST_LENGTH:
                            example_post = example_post[:MAX_EXAMPLE_POST_LENGTH] + "... [truncated]"
                            examples_truncated += 1
                        
                        # If labels is an array, format it properly
                        if isinstance(example_labels, list) and len(example_labels) > 0:
                            label_display = ", ".join(example_labels)
                        elif example_label:
                            label_display = example_label
                        else:
                            label_display = ""
                        
                        if example_post and label_display:
                            # Build this example
                            example_formatted = f"\n\nExample {idx + 1}:\nPost: {example_post}\nLabel(s): {label_display}"
                            if example_score:
                                example_formatted += f" (Score: {example_score})"
                            
                            # Check if adding this example would exceed the limit
                            if len(examples_text) + len(example_formatted) > MAX_EXAMPLES_LENGTH:
                                examples_skipped = len(classifier_examples) - idx
                                logger.warning(f"⚠️ Examples limit reached ({MAX_EXAMPLES_LENGTH} chars). Skipping {examples_skipped} remaining examples.")
                                break
                            
                            examples_text += example_formatted
                            examples_count += 1
            elif isinstance(classifier_examples, dict):
                # Handle dict format - process all items with truncation
                for idx, (key, value) in enumerate(classifier_examples.items()):
                    if isinstance(value, dict):
                        example_post = value.get("post") or value.get("text", "")
                        example_labels = value.get("labels", [])
                        example_label = value.get("label", key)
                        
                        # Truncate very long example posts
                        if len(example_post) > MAX_EXAMPLE_POST_LENGTH:
                            example_post = example_post[:MAX_EXAMPLE_POST_LENGTH] + "... [truncated]"
                            examples_truncated += 1
                        
                        if isinstance(example_labels, list) and len(example_labels) > 0:
                            label_display = ", ".join(example_labels)
                        elif example_label:
                            label_display = example_label
                        else:
                            label_display = key
                        
                        if example_post and label_display:
                            # Build this example
                            example_formatted = f"\n\nExample {idx + 1}:\nPost: {example_post}\nLabel(s): {label_display}"
                            
                            # Check if adding this example would exceed the limit
                            if len(examples_text) + len(example_formatted) > MAX_EXAMPLES_LENGTH:
                                remaining = len(classifier_examples) - idx
                                examples_skipped = remaining
                                logger.warning(f"⚠️ Examples limit reached ({MAX_EXAMPLES_LENGTH} chars). Skipping {examples_skipped} remaining examples.")
                                break
                            
                            examples_text += example_formatted
                            examples_count += 1
        
        # Build system prompt dynamically from PostClassifier.prompt
        system_prompt_parts = []
        
        # Start with the classifier prompt if provided (this contains all the rules and instructions)
        if classifier_prompt:
            system_prompt_parts.append(classifier_prompt)
        else:
            # Fallback if no prompt provided
            system_prompt_parts.append(f"You are a {classifier_name} classifier. Classify posts according to the available labels.")
        
        # Add available labels information
        if classifier_labels:
            labels_str = ", ".join(classifier_labels)
            system_prompt_parts.append(f"\n\nAvailable Labels: {labels_str}")
        
        # Add description if provided
        if classifier_description:
            system_prompt_parts.append(f"\n\nAdditional Context: {classifier_description}")
        
        # Add examples at the end if we have any
        if examples_text:
            system_prompt_parts.append(f"\n\nBelow are example posts with their correct classifications. Use them as ground-truth demonstrations for how to classify future posts:{examples_text}")
        
        # Combine into final system prompt
        system_prompt = "\n".join(system_prompt_parts)
        
        # Final safety check: Warn if system prompt is very long
        system_prompt_length = len(system_prompt)
        MAX_SYSTEM_PROMPT_LENGTH = int(os.getenv("MAX_SYSTEM_PROMPT_LENGTH", "100000"))
        
        if system_prompt_length > MAX_SYSTEM_PROMPT_LENGTH:
            logger.warning(f"⚠️ System prompt is very long ({system_prompt_length} chars). This may cause timeout issues.")
        
        # Log example summary
        if examples_count > 0:
            logger.info(f"📚 Test endpoint: Included {examples_count} examples in prompt" + 
                       (f" ({examples_skipped} skipped)" if examples_skipped > 0 else "") +
                       (f", {examples_truncated} posts truncated" if examples_truncated > 0 else ""))
        
        # Build user prompt (post content + output format)
        labels_str = ", ".join(classifier_labels)
        labels_list_str = ", ".join([f'"{label}"' for label in classifier_labels])
        user_prompt_parts = []
        # Classification rules are now in the system prompt, so user prompt just has the post
        user_prompt_parts.append(f"## Post to Classify\n\nPost Content:\n{post_text}")
        
        example_scores_dict = {}
        if len(classifier_labels) > 0:
            remaining = 0.15 / max(1, len(classifier_labels) - 1) if len(classifier_labels) > 1 else 0.0
            for i, label in enumerate(classifier_labels):
                example_scores_dict[label] = 0.85 if i == 0 else remaining
        example_scores_str = ",\n    ".join([f'"{k}": {v}' for k, v in example_scores_dict.items()])
        
        user_prompt_parts.append(f"""## Required Output Format

You MUST respond with a valid JSON object with EXACTLY this structure:
{{
  "label": "<one of the available labels>",
  "score": <number between 0.0 and 1.0>,
  "scores": {{
    <scores for ALL labels>
  }}
}}

REQUIREMENTS:
1. "label" must be one of these exact labels: {labels_list_str}
2. "score" must be a number between 0.0 and 1.0 representing confidence in the primary label
3. "scores" MUST be an object with ALL {len(classifier_labels)} labels as keys: {labels_list_str}
4. Each score in "scores" must be a number between 0.0 and 1.0
5. The scores MUST sum to exactly 1.0 (probability distribution)
6. The score for the primary "label" should be the highest

Example response format:
{{
  "label": "{classifier_labels[0] if classifier_labels else 'label'}",
  "score": 0.85,
  "scores": {{
    {example_scores_str}
  }}
}}

Respond ONLY with valid JSON. No markdown, no code blocks, no explanation, just the JSON object.""")
        user_prompt = "\n\n".join(user_prompt_parts)
        
        # Get model name
        model_name = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
        
        # Call Groq directly and capture everything
        debug_info = {
            "classifier_id": classifier_id,
            "classifier_name": classifier_name,
            "post_text": post_text,
            "labels": classifier_labels,
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "groq_response": None,
            "parsed_result": None,
            "classification_result": None,
            "error": None,
            "model_used": model_name
        }
        
        try:
            # Call Groq
            response = groq_client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3,
                response_format={"type": "json_object"}
            )
            
            content = response.choices[0].message.content
            debug_info["groq_response"] = content
            debug_info["groq_response_length"] = len(content)
            
            # Try to parse
            try:
                result = json.loads(content)
                debug_info["parsed_result"] = result
                debug_info["parsed_success"] = True
                
                # Try to classify
                classification = await classify_post_with_groq(
                    post={"text": post_text},
                    classifier_name=classifier_name,
                    classifier_prompt=classifier_prompt,
                    classifier_description=classifier_description,
                    classifier_labels=classifier_labels,
                    classifier_examples=classifier_examples
                )
                debug_info["classification_result"] = classification
                debug_info["success"] = True
            except json.JSONDecodeError as e:
                debug_info["parse_error"] = str(e)
                debug_info["parsed_success"] = False
        except Exception as e:
            debug_info["error"] = str(e)
            debug_info["error_type"] = type(e).__name__
            debug_info["success"] = False
            import traceback
            debug_info["traceback"] = traceback.format_exc()
        
        return debug_info
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in test endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to test classification: {str(e)}")


# === DEBUG ENDPOINT: Preview Classification Prompt ===
@app.get("/api/classifier/preview-prompt")
async def preview_classifier_prompt(
    classifier_id: str = Query(..., description="ID of the classifier"),
    sample_post_text: str = Query("This is a sample LinkedIn post about AI and machine learning.", description="Sample post text to preview the prompt")
):
    """
    Preview what prompt would be sent to Groq for classification.
    Useful for debugging without actually calling Groq API.
    """
    ensure_db_available("audience")
    
    try:
        # Fetch classifier using psycopg2
        classifier = database.find_post_classifier_by_id(classifier_id)
        if not classifier:
            raise HTTPException(status_code=404, detail=f"Classifier {classifier_id} not found")
        
        # Parse classifier data
        classifier_name = classifier.name or ''
        classifier_prompt = classifier.prompt or ""
        classifier_description = classifier.description or ""
        
        # Parse labels
        labels_raw = classifier.labels or []
        if isinstance(labels_raw, str):
            try:
                classifier_labels = json.loads(labels_raw)
            except:
                classifier_labels = [labels_raw]
        elif isinstance(labels_raw, list):
            classifier_labels = labels_raw
        elif isinstance(labels_raw, dict):
            classifier_labels = list(labels_raw.keys()) if labels_raw else []
        else:
            classifier_labels = []
        
        classifier_labels = [str(l) for l in classifier_labels if l]
        
        # Parse examples
        examples_raw = classifier.examples
        classifier_examples = None
        if examples_raw:
            if isinstance(examples_raw, dict):
                classifier_examples = examples_raw
            elif isinstance(examples_raw, str):
                try:
                    classifier_examples = json.loads(examples_raw)
                except:
                    classifier_examples = None
            elif isinstance(examples_raw, list):
                classifier_examples = examples_raw
        
        # Build examples text (same as classify_post_with_groq)
        examples_text = ""
        if classifier_examples:
            if isinstance(classifier_examples, list):
                for example in classifier_examples[:3]:
                    if isinstance(example, dict):
                        example_post = example.get("post", example.get("text", ""))
                        example_label = example.get("label", "")
                        example_score = example.get("score", "")
                        if example_post and example_label:
                            examples_text += f"\nExample Post: {example_post}\nLabel: {example_label}"
                            if example_score:
                                examples_text += f" (Score: {example_score})"
            elif isinstance(classifier_examples, dict):
                for key, value in list(classifier_examples.items())[:3]:
                    if isinstance(value, dict):
                        example_post = value.get("post", value.get("text", ""))
                        example_label = value.get("label", key)
                        if example_post and example_label:
                            examples_text += f"\nExample Post: {example_post}\nLabel: {example_label}"
        
        # Build system prompt (same as classify_post_with_groq)
        system_prompt = f"""You are a Post Usefulness Classifier.
Your job is to evaluate posts and decide whether they are USEFUL for shaping a persona's professional identity.
If the post is NOT USEFUL, you must assign one reason label from the provided available labels.

## What "USEFUL" Means
A post is USEFUL if it provides meaningful signal about the persona's professional identity, including:
- Technical interests, tools, frameworks, workflows
- Engineering or design opinions
- Industry commentary
- Project learnings or case studies
- Leadership or communication style
- Problem-solving approach
- Mentorship or team-building philosophy
- Concrete expertise or domain-specific knowledge

If a post helps understand what this persona cares about professionally, it is USEFUL.

## What "NOT USEFUL" Means
A post is NOT USEFUL if it does not contribute to understanding the persona's professional identity, or if it is irrelevant, generic, noisy, or superficial.

Common NOT USEFUL categories include:
- GEN-QUOTE: Generic motivational or inspirational quotes with no professional or domain-specific insight
- PERSONAL: Personal life updates unrelated to professional identity (vacations, birthdays, family events, festival wishes)
- PROMO: Company-level promotions or marketing content (product launches, event announcements, hiring ads, awards)
- TREND: Viral trends, memes, low-signal engagement bait, or generic polls
- REPOST: Reposted content from others with little or no original commentary
- GENERIC-ADVICE: Broad career advice or leadership platitudes not tied to the persona's domain
- OFF-DOMAIN: Topics far outside the persona's expected professional domain
- LOW-CONTENT: Vague, superficial, or filler content lacking meaningful information

## Classifier Rules
1. Choose USEFUL if the post provides professional signal — even if mildly.
2. Choose NOT USEFUL only when the post clearly adds no relevant professional insight.
3. When NOT USEFUL, assign one and only one reason label from the available labels.
4. Labels must be mutually exclusive; choose the best matching category.
5. Do not infer facts beyond what is directly stated in the post.
6. Err on the side of marking ambiguous professional posts as USEFUL, not NOT USEFUL.

## Scoring Rules
For each label you assign, you MUST provide a confidence score between 0.0 and 1.0.
CRITICAL: The sum of all scores for a single post MUST be exactly 1.0 (probability distribution).
You must provide scores for ALL available labels, and they must sum to 1.0.

Available Labels: {", ".join(classifier_labels)}

{f"Few-Shot Examples:{examples_text}" if examples_text else ""}"""
        
        # Build user prompt (same as classify_post_with_groq)
        labels_str = ", ".join(classifier_labels)
        labels_list_str = ", ".join([f'"{label}"' for label in classifier_labels])
        
        user_prompt_parts = []
        
        # Add custom classification rules from PostClassifier.prompt if provided
        if classifier_prompt:
            user_prompt_parts.append(f"## User's Custom Classification Rules\n{classifier_prompt}")
        
        # Add classifier description if provided
        if classifier_description:
            user_prompt_parts.append(f"## Additional Context\n{classifier_description}")
        
        # Add the post to classify
        user_prompt_parts.append(f"## Post to Classify\n\nPost Content:\n{sample_post_text}")
        
        # Add output format requirements
        example_scores_dict = {}
        if len(classifier_labels) > 0:
            remaining = 0.15 / max(1, len(classifier_labels) - 1) if len(classifier_labels) > 1 else 0.0
            for i, label in enumerate(classifier_labels):
                example_scores_dict[label] = 0.85 if i == 0 else remaining
        
        example_scores_str = ",\n    ".join([f'"{k}": {v}' for k, v in example_scores_dict.items()])
        
        user_prompt_parts.append(f"""## Required Output Format

You MUST respond with a valid JSON object with EXACTLY this structure:
{{
  "label": "<one of the available labels>",
  "score": <number between 0.0 and 1.0>,
  "scores": {{
    <scores for ALL labels>
  }}
}}

REQUIREMENTS:
1. "label" must be one of these exact labels: {labels_list_str}
2. "score" must be a number between 0.0 and 1.0 representing confidence in the primary label
3. "scores" MUST be an object with ALL {len(classifier_labels)} labels as keys: {labels_list_str}
4. Each score in "scores" must be a number between 0.0 and 1.0
5. The scores MUST sum to exactly 1.0 (probability distribution)
6. The score for the primary "label" should be the highest

Example response format:
{{
  "label": "{classifier_labels[0] if classifier_labels else 'label'}",
  "score": 0.85,
  "scores": {{
    {example_scores_str}
  }}
}}

Respond ONLY with valid JSON. No markdown, no code blocks, no explanation, just the JSON object.""")
        
        user_prompt = "\n\n".join(user_prompt_parts)
        
        return {
            "classifier_id": classifier_id,
            "classifier_name": classifier_name,
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "labels": classifier_labels,
            "examples_used": bool(classifier_examples),
            "examples_count": len(classifier_examples) if isinstance(classifier_examples, list) else (len(classifier_examples) if isinstance(classifier_examples, dict) else 0),
            "system_prompt_length": len(system_prompt),
            "user_prompt_length": len(user_prompt),
            "sample_post_text": sample_post_text
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error previewing classifier prompt: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to preview prompt: {str(e)}")


# === GENERATE PROFILE SUMMARIES ENDPOINT ===
@app.post("/api/v1/audience-rooms/{audience_room_id}/generate-summaries")
async def generate_profile_summaries(audience_room_id: str):
    """
    Generate summaries, keywords, and highlights for all profiles in an audience room.
    
    Flow:
    1. Fetch all profiles in the audience room
    2. For each profile:
       - Fetch posts JSON from S3
       - Generate summary, keywords, and highlights using OpenAI
       - Update profile description JSON in S3 with the new data
    3. Process profiles in parallel to avoid timeouts
    
    Returns:
        Summary of processing results for all profiles
    """
    ensure_db_available("audience")
    if not openai_client:
        raise HTTPException(status_code=503, detail="OpenAI client not initialized. Please set OPENAI_API_KEY.")
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")
    
    try:
        # Fetch audience room and profiles
        audience_room = database.find_audience_room_by_id(audience_room_id, include_profiles=True)
        if not audience_room:
            raise HTTPException(status_code=404, detail=f"Audience room {audience_room_id} not found")
        
        profiles = audience_room.profiles
        if not profiles:
            raise HTTPException(status_code=404, detail=f"No profiles found in audience room {audience_room_id}")
        
        logger.info(f"Generating summaries for {len(profiles)} profiles in audience room {audience_room_id}")
        
        # Rate-limited batching to avoid OpenAI rate limits / context errors
        # Process max 3 profiles concurrently (optimized for Tier 2: 2M TPM)
        MAX_CONCURRENT = 3
        semaphore = asyncio.Semaphore(MAX_CONCURRENT)
        
        async def rate_limited_process(profile):
            async with semaphore:
                result = await process_profile_summary(profile, audience_room_id)
                await asyncio.sleep(0.5)  # Small delay to spread out API requests
                return result
        
        tasks = [rate_limited_process(profile) for profile in profiles]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Process results and handle exceptions
        processed_results = []
        success_count = 0
        error_count = 0
        skipped_count = 0
        
        for idx, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Error processing profile {profiles[idx].id}: {result}")
                processed_results.append({
                    "profile_id": profiles[idx].id,
                    "profile_name": profiles[idx].profileName,
                    "status": "error",
                    "reason": "exception",
                    "error": str(result)
                })
                error_count += 1
            else:
                processed_results.append(result)
                if result["status"] == "success":
                    success_count += 1
                elif result["status"] == "skipped":
                    skipped_count += 1
                else:
                    error_count += 1
        
        return {
            "audience_room_id": audience_room_id,
            "total_profiles": len(profiles),
            "success_count": success_count,
            "skipped_count": skipped_count,
            "error_count": error_count,
            "profiles": processed_results
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating profile summaries: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to generate summaries: {str(e)}")


# === GENERATE GROUP SUMMARY ENDPOINT ===
@app.post("/api/v1/audience-rooms/{audience_room_id}/generate-group-summary")
async def generate_group_summary(audience_room_id: str):
    """
    Generate a group summary and traits for an audience room based on all profile summaries.
    
    Flow:
    1. Fetch all profiles in the audience room
    2. For each profile, fetch description JSON from S3 and extract the summary
    3. Combine all profile summaries
    4. Generate a group summary using OpenAI based on the combined summaries
    5. Generate traits (5 traits with keywordTags and descriptions) based on profile summaries
    6. Update the audience room description JSON in S3 with both summary and traits fields
    
    Returns:
        The generated group summary, traits, and processing results
    """
    ensure_db_available("audience")
    if not openai_client:
        raise HTTPException(status_code=503, detail="OpenAI client not initialized. Please set OPENAI_API_KEY.")
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")
    
    try:
        # Fetch audience room and profiles
        audience_room = database.find_audience_room_by_id(audience_room_id, include_profiles=True)
        if not audience_room:
            raise HTTPException(status_code=404, detail=f"Audience room {audience_room_id} not found")
        
        profiles = audience_room.profiles
        if not profiles:
            raise HTTPException(status_code=404, detail=f"No profiles found in audience room {audience_room_id}")
        
        # Fetch audience room description JSON from S3
        if not audience_room.descriptionS3Url:
            raise HTTPException(status_code=404, detail="Description not found for this audience room")
        
        description_key = extract_s3_key_from_url(audience_room.descriptionS3Url)
        if not description_key:
            raise HTTPException(status_code=500, detail="Invalid S3 URL format for audience room description")
        
        room_description_data = fetch_json_from_s3(description_key)
        
        logger.info(f"Generating group summary for {len(profiles)} profiles in audience room {audience_room_id}")
        
        # Fetch profile summaries from S3
        profile_summaries = []
        companies = set()
        profiles_processed = 0
        profiles_skipped = 0
        
        for profile in profiles:
            try:
                if not profile.profileDescriptionS3Url:
                    logger.warning(f"Profile {profile.id} has no description URL, skipping")
                    profiles_skipped += 1
                    continue
                
                profile_key = extract_s3_key_from_url(profile.profileDescriptionS3Url)
                if not profile_key:
                    logger.warning(f"Profile {profile.id} has invalid description URL, skipping")
                    profiles_skipped += 1
                    continue
                
                profile_data = fetch_json_from_s3(profile_key)
                profile_summary = profile_data.get("summary")
                
                if not profile_summary:
                    logger.warning(f"Profile {profile.id} has no summary, skipping")
                    profiles_skipped += 1
                    continue
                
                profile_summaries.append({
                    "name": profile.profileName,
                    "summary": profile_summary,
                    "company": profile_data.get("current_company")
                })
                
                # Collect company information
                if profile_data.get("current_company"):
                    companies.add(profile_data.get("current_company"))
                
                profiles_processed += 1
                
            except Exception as e:
                logger.error(f"Error fetching profile {profile.id} description: {e}")
                profiles_skipped += 1
                continue
        
        if not profile_summaries:
            raise HTTPException(
                status_code=400, 
                detail="No profile summaries found. Please generate profile summaries first using /api/v1/audience-rooms/{audience_room_id}/generate-summaries"
            )
        
        # Combine all profile summaries
        combined_summaries = "\n\n".join([
            f"{idx + 1}. {p['name']} ({p.get('company', 'N/A')}):\n{p['summary']}"
            for idx, p in enumerate(profile_summaries)
        ])
        
        # Determine company type/context
        company_list = ", ".join(sorted(companies)) if companies else "various companies"
        company_type = company_list if len(companies) <= 3 else f"{len(companies)} companies"
        
        # Build the prompt according to the template
        user_prompt = f"""Analyze the following group of {len(profile_summaries)} profiles who work at {company_type}.

Companies represented: {company_list}

Individual Profile Summaries:
{combined_summaries}

Generate a comprehensive high-level summary (6-10 sentences) that covers:
1. Overall themes and patterns across all profiles in this group
2. Common topics, technologies, or expertise areas shared among them
3. Company culture and stage characteristics evident from their posts
4. Professional focus areas (e.g., technical depth, thought leadership, product development)
5. Industry trends or insights that emerge from the collective content
6. Unique characteristics or differentiators of this group
7. Common posting styles or engagement patterns
8. Key value propositions or strengths evident across the group

Write in a natural, engaging way that provides insights into this collective group of professionals from {company_type}.

Respond with ONLY the summary text, no JSON or formatting."""
        
        system_message = "You are an expert at analyzing groups of LinkedIn profiles and generating comprehensive, insightful high-level summaries. Write detailed, informative summaries that capture collective patterns and insights."
        
        # Generate group summary using OpenAI
        try:
            completion = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": user_prompt}
                ],
                max_tokens=1200,
                temperature=0.3,
            )
            
            group_summary = completion.choices[0].message.content.strip()
            
            # Generate traits based on profile summaries
            traits_prompt = f"""Analyze the following group of {len(profile_summaries)} profiles and generate traits in JSON format.

Individual Profile Summaries:
{combined_summaries}

Based on these profiles, generate a traits JSON object with exactly 5 traits. Each trait must have:
- title: One of these exact titles (keep them as-is):
  1. "Skills & Expertise"
  2. "Working Style"
  3. "Motivations & Values"
  4. "Pain Points & Needs"
  5. "Organizational Leadership & Psychographic Profile"

- keywordTags: An array of 4-6 specific keyword tags relevant to this group of profiles (not generic, but specific to what you observe in their summaries)
- descriptions: An array of 4-6 descriptive sentences (one per keywordTag) that explain how these tags apply to this specific group

The keywordTags and descriptions should be tailored to this specific group of profiles based on their actual summaries, not generic examples.

Return ONLY valid JSON in this exact format:
{{
  "traits": [
    {{
      "title": "Skills & Expertise",
      "keywordTags": ["tag1", "tag2", "tag3", "tag4", "tag5"],
      "descriptions": ["description1", "description2", "description3", "description4", "description5"]
    }},
    {{
      "title": "Working Style",
      "keywordTags": ["tag1", "tag2", "tag3", "tag4", "tag5"],
      "descriptions": ["description1", "description2", "description3", "description4", "description5"]
    }},
    {{
      "title": "Motivations & Values",
      "keywordTags": ["tag1", "tag2", "tag3", "tag4", "tag5"],
      "descriptions": ["description1", "description2", "description3", "description4", "description5"]
    }},
    {{
      "title": "Pain Points & Needs",
      "keywordTags": ["tag1", "tag2", "tag3", "tag4", "tag5"],
      "descriptions": ["description1", "description2", "description3", "description4", "description5"]
    }},
    {{
      "title": "Organizational Leadership & Psychographic Profile",
      "keywordTags": ["tag1", "tag2", "tag3", "tag4", "tag5"],
      "descriptions": ["description1", "description2", "description3", "description4", "description5"]
    }}
  ]
}}

Make sure the JSON is valid and properly formatted. Do not include any text before or after the JSON."""
            
            traits_system_message = "You are an expert at analyzing professional profiles and generating structured trait data. Always return valid JSON only, no additional text."
            
            try:
                traits_completion = openai_client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": traits_system_message},
                        {"role": "user", "content": traits_prompt}
                    ],
                    max_tokens=2000,
                    temperature=0.3,
                )
                
                traits_response = traits_completion.choices[0].message.content.strip()
                
                # Parse the JSON response
                # Sometimes the response might have markdown code blocks, so we need to extract JSON
                if "```json" in traits_response:
                    traits_response = traits_response.split("```json")[1].split("```")[0].strip()
                elif "```" in traits_response:
                    traits_response = traits_response.split("```")[1].split("```")[0].strip()
                
                traits_data = json.loads(traits_response)
                
                # Validate that we have the expected structure
                if not isinstance(traits_data, dict) or "traits" not in traits_data:
                    raise ValueError("Invalid traits JSON structure: missing 'traits' key")
                
                if not isinstance(traits_data["traits"], list) or len(traits_data["traits"]) != 5:
                    raise ValueError(f"Invalid traits JSON structure: expected 5 traits, got {len(traits_data.get('traits', []))}")
                
                # Validate each trait has the required fields
                required_titles = [
                    "Skills & Expertise",
                    "Working Style",
                    "Motivations & Values",
                    "Pain Points & Needs",
                    "Organizational Leadership & Psychographic Profile"
                ]
                
                received_titles = [trait.get("title") for trait in traits_data["traits"]]
                if set(received_titles) != set(required_titles):
                    raise ValueError(f"Invalid trait titles. Expected: {required_titles}, Got: {received_titles}")
                
                for trait in traits_data["traits"]:
                    if "keywordTags" not in trait or "descriptions" not in trait:
                        raise ValueError(f"Trait '{trait.get('title')}' missing required fields")
                    if not isinstance(trait["keywordTags"], list) or not isinstance(trait["descriptions"], list):
                        raise ValueError(f"Trait '{trait.get('title')}' has invalid keywordTags or descriptions format")
                    if len(trait["keywordTags"]) != len(trait["descriptions"]):
                        raise ValueError(f"Trait '{trait.get('title')}' has mismatched keywordTags and descriptions counts")
                
                logger.info(f"Successfully generated traits for audience room {audience_room_id}")
                
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse traits JSON: {e}")
                logger.error(f"Traits response: {traits_response[:500]}")
                raise HTTPException(status_code=500, detail=f"Failed to parse traits JSON: {str(e)}")
            except ValueError as e:
                logger.error(f"Invalid traits structure: {e}")
                raise HTTPException(status_code=500, detail=f"Invalid traits structure: {str(e)}")
            except Exception as e:
                logger.error(f"Error generating traits: {e}")
                raise HTTPException(status_code=500, detail=f"Failed to generate traits: {str(e)}")
            
            # Update audience room description JSON with the summary and traits
            room_description_data["summary"] = group_summary
            room_description_data["traits"] = traits_data["traits"]
            
            # Upload updated description back to S3
            updated_description_url = upload_json_to_s3(description_key, room_description_data)
            
            # Update the audience room record with the new URL (same key, but updated content)
            database.update_audience_room(audience_room_id, {"descriptionS3Url": updated_description_url})
            
            return {
                "audience_room_id": audience_room_id,
                "audience_room_name": audience_room.name,
                "summary": group_summary,
                "traits": traits_data["traits"],
                "total_profiles": len(profiles),
                "profiles_processed": profiles_processed,
                "profiles_skipped": profiles_skipped,
                "companies_represented": list(companies),
                "description_s3_url": updated_description_url
            }
            
        except Exception as e:
            logger.error(f"Error generating group summary: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to generate group summary: {str(e)}")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating group summary: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to generate group summary: {str(e)}")


# === REMOVE LABELS FROM POSTS ENDPOINT ===
@app.post("/api/v1/audience-rooms/{audience_room_id}/remove-labels")
async def remove_labels_from_posts(audience_room_id: str):
    """
    Remove the 'labels' field from all posts JSON for all profiles in an audience room.
    
    Flow:
    1. Fetch all profiles in the audience room
    2. For each profile with postsS3Url:
       - Fetch posts JSON from S3
       - Remove 'labels' field from each post
       - Upload updated JSON back to S3
       - Update the profile record in the database
    
    Returns:
        Summary of profiles processed and posts updated
    """
    ensure_db_available("audience")
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")
    
    try:
        # Fetch audience room and profiles
        audience_room = database.find_audience_room_by_id(audience_room_id, include_profiles=True)
        if not audience_room:
            raise HTTPException(status_code=404, detail=f"Audience room {audience_room_id} not found")
        
        profiles = audience_room.profiles
        if not profiles:
            raise HTTPException(status_code=404, detail=f"No profiles found in audience room {audience_room_id}")
        
        logger.info(f"Removing labels from posts for {len(profiles)} profiles in audience room {audience_room_id}")
        
        processed_profiles = []
        total_posts_updated = 0
        profiles_skipped = 0
        profiles_with_errors = 0
        
        for profile in profiles:
            profile_id = profile.id
            profile_name = profile.profileName
            
            # Skip if no posts URL
            if not profile.postsS3Url:
                logger.warning(f"Profile {profile_id} ({profile_name}) has no posts URL, skipping")
                profiles_skipped += 1
                processed_profiles.append({
                    "profile_id": profile_id,
                    "profile_name": profile_name,
                    "status": "skipped",
                    "reason": "no_posts_url",
                    "posts_updated": 0
                })
                continue
            
            try:
                # Extract S3 key and fetch posts
                posts_key = extract_s3_key_from_url(profile.postsS3Url)
                if not posts_key:
                    logger.error(f"Invalid S3 URL format for profile {profile_id}: {profile.postsS3Url}")
                    profiles_with_errors += 1
                    processed_profiles.append({
                        "profile_id": profile_id,
                        "profile_name": profile_name,
                        "status": "error",
                        "reason": "invalid_s3_url",
                        "posts_updated": 0
                    })
                    continue
                
                # Fetch posts JSON from S3
                posts_data = fetch_json_from_s3(posts_key)
                
                # Extract posts array (could be in different formats)
                posts = []
                original_structure = None
                if isinstance(posts_data, dict):
                    posts = posts_data.get("posts", [])
                    if not posts and isinstance(posts_data.get("data"), list):
                        posts = posts_data["data"]
                    original_structure = posts_data
                elif isinstance(posts_data, list):
                    posts = posts_data
                    original_structure = posts
                
                if not posts:
                    logger.warning(f"No posts found for profile {profile_id}")
                    profiles_skipped += 1
                    processed_profiles.append({
                        "profile_id": profile_id,
                        "profile_name": profile_name,
                        "status": "skipped",
                        "reason": "no_posts",
                        "posts_updated": 0
                    })
                    continue
                
                # Remove 'labels' field from each post
                posts_updated_count = 0
                for post in posts:
                    if isinstance(post, dict) and "labels" in post:
                        del post["labels"]
                        posts_updated_count += 1
                
                if posts_updated_count == 0:
                    logger.info(f"No labels found in posts for profile {profile_id}")
                    processed_profiles.append({
                        "profile_id": profile_id,
                        "profile_name": profile_name,
                        "status": "skipped",
                        "reason": "no_labels_found",
                        "posts_updated": 0
                    })
                    continue
                
                # Update the posts data structure
                if isinstance(original_structure, dict):
                    original_structure["posts"] = posts
                    updated_posts_data = original_structure
                else:
                    updated_posts_data = posts
                
                # Upload updated posts back to S3
                updated_posts_url = upload_json_to_s3(posts_key, updated_posts_data)
                
                # Update profile record with new posts URL (same key, but updated content)
                database.update_audience_profile(profile_id, {"postsS3Url": updated_posts_url})
                
                total_posts_updated += posts_updated_count
                processed_profiles.append({
                    "profile_id": profile_id,
                    "profile_name": profile_name,
                    "status": "success",
                    "posts_updated": posts_updated_count,
                    "updated_posts_url": updated_posts_url
                })
                
                logger.info(f"Removed labels from {posts_updated_count} posts for profile {profile_id}")
                
            except Exception as e:
                logger.error(f"Error processing profile {profile_id}: {e}")
                profiles_with_errors += 1
                processed_profiles.append({
                    "profile_id": profile_id,
                    "profile_name": profile_name,
                    "status": "error",
                    "reason": str(e),
                    "posts_updated": 0
                })
        
        return {
            "audience_room_id": audience_room_id,
            "audience_room_name": audience_room.name,
            "total_profiles": len(profiles),
            "total_posts_updated": total_posts_updated,
            "profiles_processed": len([p for p in processed_profiles if p["status"] == "success"]),
            "profiles_skipped": profiles_skipped,
            "profiles_with_errors": profiles_with_errors,
            "profiles": processed_profiles
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error removing labels from posts: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to remove labels from posts: {str(e)}")


# === STEP 4: TRIGGER POST SCRAPER (ASYNC) ===
@app.post("/api/v1/scrape")
async def trigger_scraping(payload: ScrapeRequest):
    """
    Triggers Apify Actor (linkedin-post-search-scraper) asynchronously.
    Creates a job record and starts the Apify actor without waiting for completion.
    Returns job_id immediately for polling status.
    """
    # Convert Cookie Pydantic models to dictionaries for Apify
    cookies_dict = [cookie.dict(exclude_none=True) for cookie in payload.cookies]

    # Normalize LinkedIn URLs for database storage (for matching later)
    normalized_urls = [u for u in (normalize_linkedin_url(u) for u in payload.linkedin_urls) if u]
    
    # Prepare URLs for Apify - ensure they have full https://www.linkedin.com format
    apify_urls = []
    for url in payload.linkedin_urls:
        url = url.strip()
        # Ensure it has https://
        if not url.startswith("http://") and not url.startswith("https://"):
            url = "https://" + url
        # Ensure it has www. if it's just linkedin.com
        if url.startswith("https://linkedin.com") and not url.startswith("https://www.linkedin.com"):
            url = url.replace("https://linkedin.com", "https://www.linkedin.com")
        elif url.startswith("http://linkedin.com") and not url.startswith("http://www.linkedin.com"):
            url = url.replace("http://linkedin.com", "https://www.linkedin.com")
        apify_urls.append(url)

    run_input = {
        "urls": apify_urls,
        "limitPerSource": payload.max_posts,
        "cookie": cookies_dict,
        "userAgent": payload.user_agent,
        "proxy": {"useApifyProxy": True}
    }

    # Check if database is available
    ensure_db_available("main")
    
    try:
        # Create job record in database
        job = database.create_scrape_job(
            linkedin_urls=normalized_urls,
            max_posts=payload.max_posts,
            audience_room_id=payload.audience_room_id,
        )
        job_id = job.id
        
        logger.info(f"Created job {job_id} for {len(payload.linkedin_urls)} URLs")
        
        # Start Apify actor without waiting (async)
        try:
            run = apify_client.actor(POST_SCRAPER_ACTOR_ID).start(run_input=run_input)
            run_data = None
            # Apify client may return {"data": {...}} or the payload directly; handle both
            if isinstance(run, dict):
                run_data = run.get("data", run)
            if not run_data or not isinstance(run_data, dict):
                raise HTTPException(status_code=502, detail="Apify start returned unexpected response structure")
            apify_run_id = run_data.get('id')
            if not apify_run_id:
                raise HTTPException(status_code=502, detail="Apify start did not return a run id")
            
            # Update job with Apify run ID and set status to PROCESSING
            database.update_scrape_job(job_id, {
                "status": "PROCESSING",
                "apifyRunId": apify_run_id
            })
            
            logger.info(f"Started Apify run {apify_run_id} for job {job_id}")
            
            return {
                "job_id": job_id,
                "status": "PENDING",
                "message": "Scraping job started. Use /api/v1/scrape/status/{job_id} to check progress."
            }
        except Exception as apify_error:
            # If Apify start fails, mark job as failed
            error_message = str(apify_error)
            database.update_scrape_job(job_id, {
                "status": "FAILED",
                "error": error_message
            })
            raise apify_error
            
    except Exception as e:
        error_message = str(e)
        error_type = type(e).__name__
        logger.error(f"Apify error [{error_type}]: {error_message}")
        
        # Handle specific error types - check multiple variations
        error_lower = error_message.lower()
        error_repr = repr(e).lower()
        
        # Check for usage/rate limit errors (check both message and repr)
        if any(keyword in error_lower or keyword in error_repr 
               for keyword in ["usage", "limit exceeded", "quota", "hard limit", "monthly usage"]):
            status_code = 429
            detail = {
                "error": "Apify usage limit exceeded",
                "message": error_message,
                "suggestion": "Please check your Apify account usage limits or upgrade your plan. You can check your usage at https://console.apify.com/usage"
            }
        # Check for authentication errors
        elif any(keyword in error_lower or keyword in error_repr 
                 for keyword in ["unauthorized", "authentication", "token", "invalid api", "api key"]):
            status_code = 401
            detail = {
                "error": "Apify authentication failed",
                "message": error_message,
                "suggestion": "Please check your APIFY_API_TOKEN environment variable."
            }
        # Check for actor not found errors
        elif any(keyword in error_lower or keyword in error_repr 
                 for keyword in ["not found", "actor", "404", "does not exist"]):
            status_code = 404
            detail = {
                "error": "Apify actor not found or inaccessible",
                "message": error_message,
                "suggestion": f"Please verify the actor ID: {POST_SCRAPER_ACTOR_ID}"
            }
        # Check for rate limiting (429 from Apify itself)
        elif "429" in error_message or "rate limit" in error_lower or "too many requests" in error_lower:
            status_code = 429
            detail = {
                "error": "Apify rate limit exceeded",
                "message": error_message,
                "suggestion": "Please wait a moment and try again, or check your Apify account rate limits."
            }
        # Generic server error
        else:
            status_code = 500
            detail = {
                "error": "Apify service error",
                "message": error_message,
                "error_type": error_type
            }
        
        raise HTTPException(status_code=status_code, detail=detail)

# === STEP 5: CHECK SCRAPE JOB STATUS ===
@app.get("/api/v1/scrape/status/{job_id}")
async def get_scrape_status(job_id: str = Path(..., description="Job ID returned from /api/v1/scrape")):
    """
    Check the status of a scraping job.
    If job is PENDING or PROCESSING, checks Apify API for completion.
    If completed, fetches results and updates database.
    """
    # Check if database is available
    ensure_db_available("main")
    
    try:
        # Get job from database
        job = database.find_scrape_job_by_id(job_id)
        
        if not job:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
        
        # If already completed, attempt audience post backfill if dataset is available; otherwise return cached results
        if job.status == "COMPLETED":
            result_data = job.result if isinstance(job.result, dict) else {}
            dataset_id = None
            if result_data:
                dataset_id = (
                    result_data.get("dataset_id")
                    or result_data.get("defaultDatasetId")
                    or result_data.get("datasetId")
                )
            
            # If dataset_id not in result, try to fetch it from Apify using the stored run ID
            if not dataset_id and job.apifyRunId:
                try:
                    run = apify_client.run(job.apifyRunId).get()
                    run_data = run.get("data", run) if isinstance(run, dict) else {}
                    dataset_id = run_data.get("defaultDatasetId") if isinstance(run_data, dict) else None
                    logger.info(f"Fetched dataset_id {dataset_id} from Apify for completed job {job_id}")
                except Exception as apify_fetch_error:
                    logger.warning(f"Could not fetch dataset_id from Apify for job {job_id}: {apify_fetch_error}")

            # Try to process posts and update profiles if dataset is available
            if dataset_id and database.is_audience_db_available():
                try:
                    # Get LinkedIn URLs from job (handle different storage formats)
                    linkedin_urls_list = []
                    if job.linkedinUrls:
                        # JSON fields are returned as Python objects
                        if isinstance(job.linkedinUrls, list):
                            linkedin_urls_list = job.linkedinUrls
                        else:
                            # Try to convert to list if it's not already
                            try:
                                linkedin_urls_list = list(job.linkedinUrls) if hasattr(job.linkedinUrls, '__iter__') and not isinstance(job.linkedinUrls, str) else []
                            except (TypeError, ValueError):
                                linkedin_urls_list = []
                    
                    dataset_client = apify_client.dataset(dataset_id)
                    processing_result = await process_posts_and_update_profiles(
                        dataset_client=dataset_client,
                        job_id=job_id,
                        audience_room_id=job.audienceRoomId,
                        linkedin_urls=linkedin_urls_list if linkedin_urls_list else None,
                    )
                    
                    # Update job result with processing info
                    new_result = {
                        "dataset_id": dataset_id,
                        **processing_result,
                    }
                    database.update_scrape_job(job_id, {"result": new_result})
                    
                    return {
                        "job_id": job_id,
                        "status": "COMPLETED",
                        "audience_room_id": job.audienceRoomId,
                        "posts_found": processing_result.get("posts_found", 0),
                        "profiles_updated": processing_result.get("profiles_updated", 0),
                        "profiles_missing": processing_result.get("profiles_missing", 0),
                        "updated": processing_result.get("updated", []),
                        "missing": processing_result.get("missing", []),
                        "created_at": job.createdAt.isoformat() if job.createdAt else datetime.now().isoformat(),
                        "updated_at": datetime.now().isoformat(),
                    }
                except Exception as backfill_error:
                    logger.error(f"Audience backfill on completed job failed: {backfill_error}")

            return {
                "job_id": job_id,
                "status": "COMPLETED",
                "result": job.result if job.result else {},
                "created_at": job.createdAt.isoformat(),
                "updated_at": job.updatedAt.isoformat()
            }
        
        # If failed, return error
        if job.status == "FAILED":
            return {
                "job_id": job_id,
                "status": "FAILED",
                "error": job.error,
                "created_at": job.createdAt.isoformat(),
                "updated_at": job.updatedAt.isoformat()
            }
        
        # If PENDING or PROCESSING, check Apify status
        if not job.apifyRunId:
            return {
                "job_id": job_id,
                "status": job.status,
                "message": "Waiting for Apify run to start..."
            }
        
        try:
            # Check Apify run status
            run = apify_client.run(job.apifyRunId).get()
            run_data = run.get("data", run) if isinstance(run, dict) else {}
            run_status = run_data.get("status") if isinstance(run_data, dict) else None
            dataset_id = run_data.get("defaultDatasetId") if isinstance(run_data, dict) else None
            
            # If Apify didn't return a status but we already have a dataset, treat it as success
            if run_status == 'SUCCEEDED' or (not run_status and dataset_id):
                if not dataset_id:
                    return {
                        "job_id": job_id,
                        "status": "PROCESSING",
                        "message": "Run completed but dataset not available yet"
                    }

                dataset_client = apify_client.dataset(dataset_id)

                # Get LinkedIn URLs from job for matching profiles (handle different storage formats)
                linkedin_urls_list = []
                if job.linkedinUrls:
                    # Prisma JSON fields are typically returned as Python objects
                    if isinstance(job.linkedinUrls, list):
                        linkedin_urls_list = job.linkedinUrls
                    else:
                        # Try to convert to list if it's not already
                        try:
                            linkedin_urls_list = list(job.linkedinUrls) if hasattr(job.linkedinUrls, '__iter__') and not isinstance(job.linkedinUrls, str) else []
                        except (TypeError, ValueError):
                            linkedin_urls_list = []

                # Process posts and update profiles (works with or without audienceRoomId)
                processing_result = await process_posts_and_update_profiles(
                    dataset_client=dataset_client,
                    job_id=job_id,
                    audience_room_id=job.audienceRoomId,
                    linkedin_urls=linkedin_urls_list if linkedin_urls_list else None,
                )

                database.update_scrape_job(job_id, {
                    "status": "COMPLETED",
                    "result": {
                        "dataset_id": dataset_id,
                        **processing_result,
                    },
                })

                return {
                    "job_id": job_id,
                    "status": "COMPLETED",
                    "posts_found": processing_result.get("posts_found", 0),
                    "audience_room_id": job.audienceRoomId,
                    "profiles_updated": processing_result.get("profiles_updated", 0),
                    "profiles_missing": processing_result.get("profiles_missing", 0),
                    "updated": processing_result.get("updated", []),
                    "missing": processing_result.get("missing", []),
                    "created_at": job.createdAt.isoformat() if job.createdAt else datetime.now().isoformat(),
                    "updated_at": datetime.now().isoformat(),
                }
            elif run_status == 'FAILED':
                error_msg = run.get('data', {}).get('statusMessage', 'Unknown error')
                database.update_scrape_job(job_id, {
                    "status": "FAILED",
                    "error": f"Apify run failed: {error_msg}"
                })
                return {
                    "job_id": job_id,
                    "status": "FAILED",
                    "error": error_msg
                }
            elif run_status in ['RUNNING', 'READY']:
                return {
                    "job_id": job_id,
                    "status": "PROCESSING",
                    "apify_status": run_status,
                    "message": "Scraping in progress..."
                }
            else:
                return {
                    "job_id": job_id,
                    "status": "PROCESSING",
                    "apify_status": run_status,
                    "message": f"Run status: {run_status}"
                }
        except Exception as apify_error:
            logger.error(f"Error checking Apify status for job {job_id}: {apify_error}")
            return {
                "job_id": job_id,
                "status": job.status,
                "message": f"Error checking Apify status: {str(apify_error)}"
            }
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting job status: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)