import os
import json
import logging
import uuid
import asyncio
from typing import List, Optional, Dict, Any
from datetime import datetime
from dateutil.parser import parse, ParserError

# FastAPI Imports
from fastapi import FastAPI, HTTPException, Body, Path
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

# Prisma client - import from local directory (generated during build)
Prisma = None
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

# Initialize Clients
pdl_client = None
apify_client = None
openai_client = None
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
    # Prisma client for database operations
    if Prisma is not None:
        prisma = Prisma()
        logger.info("Prisma client initialized successfully")
    else:
        prisma = None
        # Only warn if we're in an environment where database is expected
        # On Vercel, if build generation worked, this shouldn't happen
        if os.getenv("VERCEL"):
            logger.warning("Prisma client not available on Vercel - check build logs for 'prisma generate'")
        else:
            logger.warning("Prisma client not available - database features will be disabled")
except Exception as e:
    logger.error(f"Failed to initialize Prisma client: {e}")
    prisma = None

try:
    # Separate Prisma client for the audience database (uses AUDIENCE_DATABASE_URL or PRISMA_DATABASE_URL)
    if Prisma is not None and audience_db_url:
        audience_prisma = Prisma(datasource={"url": audience_db_url, "name": "db"})
        logger.info("Audience Prisma client initialized successfully")
    elif Prisma is not None:
        logger.warning("AUDIENCE_DATABASE_URL not set; audience endpoints will be disabled")
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
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify your frontend domain (e.g., ["http://localhost:3000"])
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


def generate_presigned_get_url(key: str, expires_in: int = 86400) -> Optional[str]:
    """Generate a presigned GET URL for an S3 object."""
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


def normalize_linkedin_url(url: str) -> Optional[str]:
    """Normalize LinkedIn profile URLs for matching."""
    if not url:
        return None
    url = url.strip().lower()
    # Add protocol if missing
    if url.startswith("linkedin.com"):
        url = "https://" + url
    # Remove trailing slashes
    while url.endswith("/"):
        url = url[:-1]
    return url

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
    description_presigned = generate_presigned_get_url(description_key)

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
        profile_presigned = generate_presigned_get_url(profile_key)

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
            "description_presigned_url": description_presigned,
            "profiles_created": len(room.profiles),
            "profiles": [
                {
                    "profile_id": p.id,
                    "profile_name": p.profileName,
                    "linkedin_url": p.linkedinUrl,
                    "profile_description_s3_url": p.profileDescriptionS3Url,
                    "profile_description_presigned_url": generate_presigned_get_url(
                        f"audiences/{room.id}/profiles/{p.id}/profile.json"
                    ),
                    "posts_s3_url": p.postsS3Url,
                    "posts_presigned_url": generate_presigned_get_url(
                        f"audiences/{room.id}/profiles/{p.id}/posts.json"
                    )
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
    posts_presigned = generate_presigned_get_url(posts_key)

    try:
        updated = await audience_prisma.audienceprofile.update(
            where={"id": profile_id},
            data={"postsS3Url": posts_url},
        )
        return {
            "profile_id": updated.id,
            "audience_room_id": audience_room_id,
            "posts_s3_url": updated.postsS3Url,
            "posts_presigned_url": posts_presigned,
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
        select={"id": True, "profileName": True, "linkedinUrl": True},
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
        norm_profile_url = normalize_linkedin_url(p["linkedinUrl"])
        if not norm_profile_url:
            missing.append({"profile_id": p["id"], "profile_name": p["profileName"], "reason": "missing_linkedin_url"})
            continue

        posts_for_profile = posts_by_url.get(norm_profile_url, [])
        if not posts_for_profile:
            missing.append({"profile_id": p["id"], "profile_name": p["profileName"], "reason": "no_posts_found"})
            continue

        posts_key = f"audiences/{audience_room_id}/profiles/{p['id']}/posts.json"
        posts_url = upload_json_to_s3(
            posts_key,
            {
                "profile_id": p["id"],
                "audience_room_id": audience_room_id,
                "linkedin_profile_url": p["linkedinUrl"],
                "posts": posts_for_profile,
            },
        )
        posts_presigned = generate_presigned_get_url(posts_key)

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
                    "posts_presigned_url": posts_presigned,
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

    # Normalize LinkedIn URLs
    normalized_urls = [u for u in (normalize_linkedin_url(u) for u in payload.linkedin_urls) if u]

    run_input = {
        "urls": normalized_urls,
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
        
        # If already completed, return cached results
        if job.status == "COMPLETED":
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

                # If the job is tied to an audience room, stream directly from Apify -> S3 per profile
                if job.audienceRoomId:
                    if not audience_prisma:
                        raise HTTPException(status_code=503, detail="Audience database connection not available. Please set AUDIENCE_DATABASE_URL.")
                    await ensure_prisma_connection(audience_prisma, "Audience")

                    profiles = await audience_prisma.audienceprofile.find_many(
                        where={"audienceRoomId": job.audienceRoomId},
                        select={"id": True, "profileName": True, "linkedinUrl": True},
                    )

                    profile_by_url: Dict[str, Dict[str, str]] = {}
                    for p in profiles:
                        norm_url = normalize_linkedin_url(p["linkedinUrl"])
                        if norm_url:
                            profile_by_url[norm_url] = p

                    posts_acc: Dict[str, List[Any]] = {p["id"]: [] for p in profiles}
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

                    updated = []
                    missing = []
                    for p in profiles:
                        pid = p["id"]
                        posts_for_profile = posts_acc.get(pid, [])
                        if not posts_for_profile:
                            missing.append({"profile_id": pid, "profile_name": p["profileName"], "reason": "no_posts_found"})
                            continue

                        posts_key = f"audiences/{job.audienceRoomId}/profiles/{pid}/posts.json"
                        posts_url = upload_json_to_s3(
                            posts_key,
                            {
                                "profile_id": pid,
                                "audience_room_id": job.audienceRoomId,
                                "linkedin_profile_url": p["linkedinUrl"],
                                "posts": posts_for_profile,
                            },
                        )
                        try:
                            await audience_prisma.audienceprofile.update(
                                where={"id": pid},
                                data={"postsS3Url": posts_url},
                            )
                            updated.append(
                                {
                                    "profile_id": pid,
                                    "profile_name": p["profileName"],
                                    "linkedin_url": p["linkedinUrl"],
                                    "posts_s3_url": posts_url,
                                }
                            )
                        except Exception as e:
                            logger.error(f"Error updating posts for profile {pid}: {e}")
                            missing.append({"profile_id": pid, "profile_name": p["profileName"], "reason": "db_update_failed"})

                    await prisma.scrapejob.update(
                        where={"id": job_id},
                        data={
                            "status": "COMPLETED",
                            "result": {
                                "posts_found": total_items,
                                "profiles_updated": len(updated),
                                "profiles_missing": len(missing),
                                "dataset_id": dataset_id,
                            },
                        },
                    )

                    return {
                        "job_id": job_id,
                        "status": "COMPLETED",
                        "posts_found": total_items,
                        "audience_room_id": job.audienceRoomId,
                        "profiles_updated": len(updated),
                        "profiles_missing": len(missing),
                        "updated": updated,
                        "missing": missing,
                        "created_at": job.createdAt.isoformat(),
                        "updated_at": datetime.now().isoformat(),
                    }

                # If no audience room, just count items and mark complete without storing the blob
                total_items = 0
                for _ in dataset_client.iterate_items():
                    total_items += 1

                await prisma.scrapejob.update(
                    where={"id": job_id},
                    data={
                        "status": "COMPLETED",
                        "result": {
                            "posts_found": total_items,
                            "dataset_id": dataset_id,
                        },
                    },
                )

                return {
                    "job_id": job_id,
                    "status": "COMPLETED",
                    "posts_found": total_items,
                    "dataset_id": dataset_id,
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