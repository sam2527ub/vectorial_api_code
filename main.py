import os
import json
import logging
import uuid
import asyncio
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

# Prisma client - import from local directory (generated during build)
Prisma = None
AudiencePrisma = None
try:
    # Try importing generated client from local prisma_client package first
    import sys
    from pathlib import Path as PathLib  # Use alias to avoid conflict with FastAPI's Path
    prisma_client_path = PathLib(__file__).parent / "prisma_client"
    if prisma_client_path.exists():
        sys.path.insert(0, str(prisma_client_path))

    try:
        from prisma_client import Prisma  # local generated client
        try:
            from prisma_client.fields import Json as PrismaJson
        except Exception:
            PrismaJson = None
        logger.info("Prisma client imported successfully (local prisma_client)")
    except ImportError:
        from prisma import Prisma  # fallback to installed package
        try:
            from prisma.fields import Json as PrismaJson
        except Exception:
            PrismaJson = None
        logger.info("Prisma client imported successfully (site-packages prisma)")
except (RuntimeError, ImportError) as e:
    error_msg = str(e)
    if "hasn't been generated" in error_msg or "has not been generated" in error_msg:
        # Try runtime generation as fallback (will likely fail on serverless, but try anyway)
        import subprocess
        import sys
        try:
            # Suppress output - this is expected to fail on Vercel
            subprocess.run(
                [sys.executable, "-m", "prisma", "generate"], 
                check=True, 
                capture_output=True,
                text=True,
                timeout=30
            )
            from prisma import Prisma
            logger.debug("Prisma client generated and imported at runtime")
        except Exception:
            # Runtime generation failed (expected on Vercel) - try import again
            # Client should have been generated during build
            try:
                from prisma import Prisma
                logger.debug("Prisma client imported after build (runtime generation failed as expected)")
            except Exception as import_error:
                # Client is truly unavailable
                Prisma = None
                logger.debug(f"Prisma client unavailable: {import_error}")
    else:
        # Re-raise if it's a different error
        raise

# Import audience Prisma client separately
try:
    import sys
    from pathlib import Path as PathLib
    audience_prisma_client_path = PathLib(__file__).parent / "audience_prisma_client"
    if audience_prisma_client_path.exists():
        # Add parent directory to path so we can import the package
        parent_path = str(PathLib(__file__).parent)
        if parent_path not in sys.path:
            sys.path.insert(0, parent_path)
    
    try:
        import audience_prisma_client
        AudiencePrisma = audience_prisma_client.Prisma
        logger.info("Audience Prisma client imported successfully")
    except (ImportError, AttributeError) as e:
        logger.warning(f"Failed to import Audience Prisma client: {e}")
        AudiencePrisma = None
except Exception as e:
    logger.warning(f"Failed to set up Audience Prisma client path: {e}")
    AudiencePrisma = None

# Initialize Clients
pdl_client = None
apify_client = None
openai_client = None
groq_client = None
dynamodb_resource = None
prisma = None
audience_prisma = None
audience_db_url = os.getenv("AUDIENCE_DATABASE_URL")
s3_client = None
s3_bucket = os.getenv("AUDIENCE_BUCKET_NAME") or os.getenv("VECTOR_BUCKET_NAME")
s3_region = os.getenv("AWS_REGION", "us-west-2")

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
    if groq_api_key:
        groq_client = Groq(api_key=groq_api_key)
        logger.info("Groq client initialized successfully")
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

try:
    # Prisma client for main database (ScrapeJob only)
    if Prisma is not None:
        prisma = Prisma()
        logger.info("Prisma client initialized successfully")
    else:
        prisma = None
        if os.getenv("VERCEL"):
            logger.warning("Prisma client not available on Vercel - check build logs for 'prisma generate'")
        else:
            logger.warning("Prisma client not available - database features will be disabled")
except Exception as e:
    logger.error(f"Failed to initialize Prisma client: {e}")
    prisma = None

try:
    # Separate Prisma client for the audience database (uses AUDIENCE_DATABASE_URL)
    if AudiencePrisma is not None and audience_db_url:
        audience_prisma = AudiencePrisma(datasource={"url": audience_db_url, "name": "audience_db"})
        logger.info("Audience Prisma client initialized successfully")
    elif AudiencePrisma is not None:
        logger.warning("AUDIENCE_DATABASE_URL not set; audience endpoints will be disabled")
        audience_prisma = None
    else:
        logger.warning("Audience Prisma client not available; audience endpoints will be disabled")
        audience_prisma = None
except Exception as e:
    logger.error(f"Failed to initialize Audience Prisma client: {e}")
    audience_prisma = None

# Lifespan event handlers (replaces deprecated on_event)
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    if prisma:
        try:
            await prisma.connect()
            logger.info("Prisma client connected successfully")
        except Exception as e:
            logger.error(f"Failed to connect Prisma: {e}")
            logger.warning("Continuing without database connection - scraping endpoints will not work")
    if audience_prisma:
        try:
            await audience_prisma.connect()
            logger.info("Audience Prisma client connected successfully")
        except Exception as e:
            logger.error(f"Failed to connect Audience Prisma: {e}")
            logger.warning("Continuing without audience database connection")
    yield
    # Shutdown
    if prisma:
        try:
            await prisma.disconnect()
            logger.info("Prisma client disconnected")
        except Exception as e:
            logger.error(f"Error disconnecting Prisma: {e}")
    if audience_prisma:
        try:
            await audience_prisma.disconnect()
            logger.info("Audience Prisma client disconnected")
        except Exception as e:
            logger.error(f"Error disconnecting Audience Prisma: {e}")

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


async def ensure_prisma_connection(client: Prisma, client_name: str, max_retries: int = 3):
    """Ensure a Prisma client is connected with retries."""
    for attempt in range(max_retries):
        try:
            if not client.is_connected():
                await client.connect()
            return
        except Exception as conn_error:
            if attempt < max_retries - 1:
                logger.warning(f"{client_name} connection attempt {attempt + 1} failed, retrying...")
                await asyncio.sleep(0.5)
            else:
                raise HTTPException(status_code=503, detail=f"{client_name} database connection failed after {max_retries} attempts: {conn_error}")


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

def to_prisma_json(data: Any) -> Any:
    """Wrap data for Prisma Json fields if PrismaJson is available."""
    try:
        return PrismaJson(data) if "PrismaJson" in globals() and PrismaJson else data
    except Exception:
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
    if not audience_prisma:
        logger.warning("Audience database not available, skipping profile updates")
        return {"posts_found": 0, "profiles_updated": 0, "profiles_missing": 0}
    
    if not s3_client or not s3_bucket:
        logger.warning("S3 not configured, skipping profile updates")
        return {"posts_found": 0, "profiles_updated": 0, "profiles_missing": 0}
    
    await ensure_prisma_connection(audience_prisma, "Audience")
    
    # Fetch profiles - either from specific room or all profiles if URLs provided
    if audience_room_id:
        # Get profiles from specific audience room
        profiles = await audience_prisma.audienceprofile.find_many(
            where={"audienceRoomId": audience_room_id},
        )
    elif linkedin_urls:
        # Get all profiles that match any of the scraped URLs
        # We'll filter by matching normalized URLs
        all_profiles = await audience_prisma.audienceprofile.find_many()
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
            await audience_prisma.audienceprofile.update(
                where={"id": pid},
                data={"postsS3Url": posts_url},
            )
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
    if not audience_prisma:
        raise HTTPException(status_code=503, detail="Audience database connection not available. Please set AUDIENCE_DATABASE_URL.")
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")

    # Ensure the audience Prisma client is connected
    await ensure_prisma_connection(audience_prisma, "Audience")

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
        room = await audience_prisma.audienceroom.create(
            data={
                "id": room_id,
                "name": payload.audience_room_name,
                "descriptionS3Url": description_url,
                "profiles": {"create": profile_creates},
            },
            include={"profiles": True},
        )

        return {
            "audience_room_id": room.id,
            "audience_room_name": room.name,
            "description_s3_url": room.descriptionS3Url,
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


# === STEP 3C: ATTACH POSTS TO AN AUDIENCE PROFILE ===
@app.post("/api/v1/audience-rooms/{audience_room_id}/profiles/{profile_id}/posts")
async def upload_profile_posts(audience_room_id: str, profile_id: str, payload: UpdateProfilePostsRequest):
    """
    Store scraped posts JSON for a profile in S3 and update the profile record.
    S3 path: audiences/{audience_room_id}/profiles/{profile_id}/posts.json
    """
    if not audience_prisma:
        raise HTTPException(status_code=503, detail="Audience database connection not available. Please set AUDIENCE_DATABASE_URL.")
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")

    await ensure_prisma_connection(audience_prisma, "Audience")

    # Verify profile exists and belongs to the room
    profile = await audience_prisma.audienceprofile.find_unique(
        where={"id": profile_id},
        include={"audienceRoom": True},
    )
    if not profile or profile.audienceRoomId != audience_room_id:
        raise HTTPException(status_code=404, detail="Profile not found for given audience room")

    posts_key = f"audiences/{audience_room_id}/profiles/{profile_id}/posts.json"
    posts_url = upload_json_to_s3(posts_key, {"profile_id": profile_id, "audience_room_id": audience_room_id, "posts": payload.posts})

    try:
        updated = await audience_prisma.audienceprofile.update(
            where={"id": profile_id},
            data={"postsS3Url": posts_url},
        )
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
    if not audience_prisma:
        raise HTTPException(status_code=503, detail="Audience database connection not available. Please set AUDIENCE_DATABASE_URL.")
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")

    await ensure_prisma_connection(audience_prisma, "Audience")

    # Resolve posts source (payload or scrape job)
    source_posts: List[Any] = []
    if payload.posts:
        source_posts = payload.posts
    elif payload.job_id:
        if not prisma:
            raise HTTPException(status_code=503, detail="Primary database connection not available for scrape jobs.")
        await ensure_prisma_connection(prisma, "Primary")

        job = await prisma.scrapejob.find_unique(where={"id": payload.job_id})
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
    profiles = await audience_prisma.audienceprofile.find_many(
        where={"audienceRoomId": audience_room_id},
    )
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
            await audience_prisma.audienceprofile.update(
                where={"id": p["id"]},
                data={"postsS3Url": posts_url},
            )
            updated.append(
                {
                    "profile_id": p["id"],
                    "profile_name": p["profileName"],
                    "linkedin_url": p["linkedinUrl"],
                    "posts_s3_url": posts_url,
                }
            )
        except Exception as e:
            logger.error(f"Error updating posts for profile {p['id']}: {e}")
            missing.append({"profile_id": p["id"], "profile_name": p["profileName"], "reason": "db_update_failed"})

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
    if not audience_prisma:
        raise HTTPException(status_code=503, detail="Audience database connection not available. Please set AUDIENCE_DATABASE_URL.")
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")
    
    await ensure_prisma_connection(audience_prisma, "Audience")
    
    try:
        # Verify room exists
        room = await audience_prisma.audienceroom.find_unique(where={"id": audience_room_id})
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
    if not audience_prisma:
        raise HTTPException(status_code=503, detail="Audience database connection not available. Please set AUDIENCE_DATABASE_URL.")
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")
    
    await ensure_prisma_connection(audience_prisma, "Audience")
    
    try:
        # Verify profile exists and belongs to the room
        profile = await audience_prisma.audienceprofile.find_unique(
            where={"id": profile_id},
            include={"audienceRoom": True},
        )
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
    if not audience_prisma:
        raise HTTPException(status_code=503, detail="Audience database connection not available. Please set AUDIENCE_DATABASE_URL.")
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")
    
    await ensure_prisma_connection(audience_prisma, "Audience")
    
    try:
        # Verify profile exists and belongs to the room
        profile = await audience_prisma.audienceprofile.find_unique(
            where={"id": profile_id},
            include={"audienceRoom": True},
        )
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
    
    # Build few-shot examples string
    examples_text = ""
    if classifier_examples:
        if isinstance(classifier_examples, list):
            for example in classifier_examples[:3]:  # Limit to 3 examples
                if isinstance(example, dict):
                    example_post = example.get("post", example.get("text", ""))
                    example_label = example.get("label", "")
                    example_score = example.get("score", "")
                    if example_post and example_label:
                        examples_text += f"\nExample Post: {example_post}\nLabel: {example_label}"
                        if example_score:
                            examples_text += f" (Score: {example_score})"
        elif isinstance(classifier_examples, dict):
            # Handle dict format
            for key, value in list(classifier_examples.items())[:3]:
                if isinstance(value, dict):
                    example_post = value.get("post", value.get("text", ""))
                    example_label = value.get("label", key)
                    if example_post and example_label:
                        examples_text += f"\nExample Post: {example_post}\nLabel: {example_label}"
    
    # Construct the prompt
    labels_str = ", ".join(classifier_labels)
    prompt = f"""You are a classification assistant. Your task is to classify LinkedIn posts based on the following rules and labels.

Classifier: {classifier_name}

Rules/Description:
{classifier_description}

Available Labels: {labels_str}

{f"Few-Shot Examples:{examples_text}" if examples_text else ""}

Now classify the following post:

Post Content:
{post_text}

You must respond with a valid JSON object containing:
- "label": The primary/winning label (one of: {labels_str})
- "score": The confidence score for the primary label (between 0.0 and 1.0)
- "scores": An object where each key is a label name and each value is a confidence score (0.0 to 1.0) for that label. Include scores for ALL available labels: {labels_str}

Example response format:
{{
  "label": "useful",
  "score": 0.85,
  "scores": {{
    "useful": 0.85,
    "not-useful": 0.15,
    "promotional": 0.05
  }}
}}

Respond ONLY with valid JSON, no other text."""
    
    try:
        # Call Groq API
        response = groq_client.chat.completions.create(
            model="llama-3.1-70b-versatile",  # Fast and capable model
            messages=[
                {"role": "system", "content": classifier_prompt or "You are a helpful classification assistant."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,  # Lower temperature for more consistent classification
            response_format={"type": "json_object"}  # Force JSON response
        )
        
        # Parse response
        content = response.choices[0].message.content
        result = json.loads(content)
        
        # Validate and normalize response
        label = result.get("label", "")
        score = result.get("score", 0.0)
        all_scores = result.get("scores", {})
        
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
            else:
                # Default to first label if no match
                logger.warning(f"Label '{label}' not in available labels, defaulting to '{classifier_labels[0]}'")
                label = classifier_labels[0]
        
        # Ensure score is between 0 and 1
        try:
            score = float(score)
            if score > 1.0:
                score = score / 100.0  # Convert percentage to decimal
            score = max(0.0, min(1.0, score))
        except (ValueError, TypeError):
            score = 0.5  # Default score
        
        # Normalize all scores - ensure we have scores for all labels
        normalized_scores = {}
        if isinstance(all_scores, dict):
            for available_label in classifier_labels:
                if available_label in all_scores:
                    try:
                        label_score = float(all_scores[available_label])
                        if label_score > 1.0:
                            label_score = label_score / 100.0
                        normalized_scores[available_label] = round(max(0.0, min(1.0, label_score)), 2)
                    except (ValueError, TypeError):
                        # If score is invalid, use 0.0
                        normalized_scores[available_label] = 0.0
                else:
                    # If label not in scores, default to 0.0
                    normalized_scores[available_label] = 0.0
        else:
            # If scores not provided or invalid, create default scores
            for available_label in classifier_labels:
                normalized_scores[available_label] = 0.0
            # Set the primary label's score
            normalized_scores[label] = round(score, 2)
        
        return {
            "label": label,
            "score": round(score, 2),
            "allScores": normalized_scores
        }
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Groq JSON response: {e}, content: {content if 'content' in locals() else 'N/A'}")
        # Return default classification with all scores set to 0 except the first
        default_label = classifier_labels[0] if classifier_labels else "Unknown"
        default_scores = {label: 0.0 for label in classifier_labels}
        if default_label in default_scores:
            default_scores[default_label] = 0.5
        return {
            "label": default_label,
            "score": 0.5,
            "allScores": default_scores
        }
    except Exception as e:
        logger.error(f"Error classifying post with Groq: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to classify post: {str(e)}")


async def classify_posts_batch(
    posts: List[Dict[str, Any]],
    classifier_name: str,
    classifier_prompt: str,
    classifier_description: str,
    classifier_labels: List[str],
    classifier_examples: Optional[Dict[str, Any]] = None,
    batch_size: int = 10,
) -> List[Dict[str, Any]]:
    """
    Classify multiple posts in parallel batches using Groq.
    
    Args:
        posts: List of post objects to classify
        classifier_name: Name of the classifier
        classifier_prompt: System prompt for the classifier
        classifier_description: Rules/description for classification
        classifier_labels: Available labels
        classifier_examples: Few-shot examples
        batch_size: Number of posts to process in parallel
    
    Returns:
        List of classification results (dicts with 'label' and 'score')
    """
    results = []
    
    # Process in batches to avoid overwhelming the API
    for i in range(0, len(posts), batch_size):
        batch = posts[i:i + batch_size]
        
        # Create tasks for parallel processing
        tasks = [
            classify_post_with_groq(
                post=post,
                classifier_name=classifier_name,
                classifier_prompt=classifier_prompt,
                classifier_description=classifier_description,
                classifier_labels=classifier_labels,
                classifier_examples=classifier_examples,
            )
            for post in batch
        ]
        
        # Execute batch in parallel
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Handle any exceptions in batch results
        for idx, result in enumerate(batch_results):
            if isinstance(result, Exception):
                logger.error(f"Error classifying post {i + idx}: {result}")
                # Use default classification on error with all scores
                default_label = classifier_labels[0] if classifier_labels else "Unknown"
                default_scores = {label: 0.0 for label in classifier_labels}
                if default_label in default_scores:
                    default_scores[default_label] = 0.5
                results.append({
                    "label": default_label,
                    "score": 0.5,
                    "allScores": default_scores
                })
            else:
                results.append(result)
    
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
    if not audience_prisma:
        raise HTTPException(status_code=503, detail="Audience database connection not available. Please set AUDIENCE_DATABASE_URL.")
    if not groq_client:
        raise HTTPException(status_code=503, detail="Groq client not initialized. Please set GROQ_API_KEY.")
    if not s3_client or not s3_bucket:
        raise HTTPException(status_code=503, detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.")
    
    await ensure_prisma_connection(audience_prisma, "Audience")
    
    try:
        # 1. Fetch Classifier details using raw query to avoid Prisma Json field conversion issues
        # Prisma sometimes has trouble with JSON fields, so we'll use a raw query
        try:
            raw_result = await audience_prisma.query_first(
                'SELECT id, name, prompt, description, labels, examples, "createdAt", "updatedAt" FROM "PostClassifier" WHERE id = $1',
                payload.classifierId
            )
        except Exception as query_error:
            # If raw query fails, try with Prisma find_unique and handle the error
            logger.warning(f"Raw query failed, trying Prisma find_unique: {query_error}")
            try:
                classifier = await audience_prisma.postclassifier.find_unique(
                    where={"id": payload.classifierId}
                )
                if not classifier:
                    raise HTTPException(status_code=404, detail=f"Classifier {payload.classifierId} not found")
                # Access labels carefully
                try:
                    _ = classifier.labels  # Try to access to trigger any conversion error
                except Exception as label_error:
                    logger.warning(f"Error accessing labels field: {label_error}")
                    # If we can't access labels, we can't proceed
                    raise HTTPException(status_code=500, detail=f"Error reading classifier labels: {str(label_error)}")
            except HTTPException:
                raise
            except Exception as prisma_error:
                raise HTTPException(status_code=500, detail=f"Failed to fetch classifier: {str(prisma_error)}")
        else:
            if not raw_result:
                raise HTTPException(status_code=404, detail=f"Classifier {payload.classifierId} not found")
            
            # Create a simple object-like structure from raw query result
            class ClassifierData:
                def __init__(self, data):
                    self.id = data.get('id')
                    self.name = data.get('name', '')
                    self.prompt = data.get('prompt')
                    self.description = data.get('description')
                    # Parse labels - could be dict, list, or string
                    labels_raw = data.get('labels')
                    if isinstance(labels_raw, str):
                        try:
                            self.labels = json.loads(labels_raw)
                        except json.JSONDecodeError:
                            self.labels = [labels_raw]  # Single label as string
                    elif isinstance(labels_raw, (list, dict)):
                        self.labels = labels_raw
                    else:
                        self.labels = []
                    # Parse examples
                    examples_raw = data.get('examples')
                    if isinstance(examples_raw, str):
                        try:
                            self.examples = json.loads(examples_raw) if examples_raw else None
                        except json.JSONDecodeError:
                            self.examples = None
                    elif examples_raw:
                        self.examples = examples_raw
                    else:
                        self.examples = None
            
            classifier = ClassifierData(raw_result)
        
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
        audience_room = await audience_prisma.audienceroom.find_unique(
            where={"id": payload.audienceRoomId},
            include={"profiles": True}
        )
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
                    batch_size=10,  # Process 10 posts in parallel
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
                await audience_prisma.audienceprofile.update(
                    where={"id": profile_id},
                    data={"postsS3Url": updated_posts_url}
                )
                
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

    # Check if Prisma is available
    if not prisma:
        raise HTTPException(status_code=503, detail="Database connection not available. Please check server configuration.")
    
    try:
        # Ensure connection is active (with retry)
        max_retries = 3
        for attempt in range(max_retries):
            try:
                if not prisma.is_connected():
                    await prisma.connect()
                break
            except Exception as conn_error:
                if attempt < max_retries - 1:
                    logger.warning(f"Connection attempt {attempt + 1} failed, retrying...")
                    await asyncio.sleep(0.5)
                else:
                    raise HTTPException(status_code=503, detail=f"Database connection failed after {max_retries} attempts")
        
        # Create job record in database
        # Wrap URLs as Prisma Json if available to satisfy type checker/runtime
        urls_value = PrismaJson(normalized_urls) if 'PrismaJson' in globals() and PrismaJson else normalized_urls

        job = await prisma.scrapejob.create(
            data={
                "status": "PENDING",
                "linkedinUrls": urls_value,
                "maxPosts": payload.max_posts,
                "audienceRoomId": payload.audience_room_id,
            }
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
            await prisma.scrapejob.update(
                where={"id": job_id},
                data={
                    "status": "PROCESSING",
                    "apifyRunId": apify_run_id
                }
            )
            
            logger.info(f"Started Apify run {apify_run_id} for job {job_id}")
            
            return {
                "job_id": job_id,
                "status": "PENDING",
                "message": "Scraping job started. Use /api/v1/scrape/status/{job_id} to check progress."
            }
        except Exception as apify_error:
            # If Apify start fails, mark job as failed
            error_message = str(apify_error)
            await prisma.scrapejob.update(
                where={"id": job_id},
                data={
                    "status": "FAILED",
                    "error": error_message
                }
            )
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
    # Check if Prisma is available
    if not prisma:
        raise HTTPException(status_code=503, detail="Database connection not available. Please check server configuration.")
    
    try:
        # Ensure connection is active (with retry)
        max_retries = 3
        for attempt in range(max_retries):
            try:
                if not prisma.is_connected():
                    await prisma.connect()
                break
            except Exception as conn_error:
                if attempt < max_retries - 1:
                    logger.warning(f"Connection attempt {attempt + 1} failed, retrying...")
                    await asyncio.sleep(0.5)
                else:
                    raise HTTPException(status_code=503, detail=f"Database connection failed after {max_retries} attempts")
        
        # Get job from database
        job = await prisma.scrapejob.find_unique(where={"id": job_id})
        
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
            if dataset_id and audience_prisma:
                try:
                    # Get LinkedIn URLs from job (handle different storage formats)
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
                    await prisma.scrapejob.update(
                        where={"id": job_id},
                        data={
                            "result": to_prisma_json(new_result),
                        },
                    )
                    
                    return {
                        "job_id": job_id,
                        "status": "COMPLETED",
                        "audience_room_id": job.audienceRoomId,
                        "posts_found": processing_result.get("posts_found", 0),
                        "profiles_updated": processing_result.get("profiles_updated", 0),
                        "profiles_missing": processing_result.get("profiles_missing", 0),
                        "updated": processing_result.get("updated", []),
                        "missing": processing_result.get("missing", []),
                        "created_at": job.createdAt.isoformat(),
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

                await prisma.scrapejob.update(
                    where={"id": job_id},
                    data={
                        "status": "COMPLETED",
                        "result": to_prisma_json({
                            "dataset_id": dataset_id,
                            **processing_result,
                        }),
                    },
                )

                return {
                    "job_id": job_id,
                    "status": "COMPLETED",
                    "posts_found": processing_result.get("posts_found", 0),
                    "audience_room_id": job.audienceRoomId,
                    "profiles_updated": processing_result.get("profiles_updated", 0),
                    "profiles_missing": processing_result.get("profiles_missing", 0),
                    "updated": processing_result.get("updated", []),
                    "missing": processing_result.get("missing", []),
                    "created_at": job.createdAt.isoformat(),
                    "updated_at": datetime.now().isoformat(),
                }
            elif run_status == 'FAILED':
                error_msg = run.get('data', {}).get('statusMessage', 'Unknown error')
                await prisma.scrapejob.update(
                    where={"id": job_id},
                    data={
                        "status": "FAILED",
                        "error": f"Apify run failed: {error_msg}"
                    }
                )
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