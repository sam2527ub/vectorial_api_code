import os
import json
import logging
import uuid
from typing import List, Optional, Dict, Any
from datetime import datetime
from dateutil.parser import parse, ParserError

# FastAPI Imports
from fastapi import FastAPI, HTTPException, Body, Path
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Third-party Clients
from dotenv import load_dotenv
from peopledatalabs import PDLPY
from apify_client import ApifyClient
from openai import OpenAI
import boto3
from prisma import Prisma

# --- 1. CONFIGURATION & SETUP ---
load_dotenv()

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Profile Engine API",
    description="Backend for PDL Enrichment, Search, and LinkedIn Scraping",
    version="1.0.0"
)

# Enable CORS (Allows your React frontend to talk to this Python backend)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify your frontend domain (e.g., ["http://localhost:3000"])
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Clients
try:
    pdl_client = PDLPY(api_key=os.getenv("PDL_API_KEY"))
    apify_client = ApifyClient(os.getenv("APIFY_API_TOKEN"))
    openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    # Optional: DynamoDB
    dynamodb_resource = boto3.resource('dynamodb', region_name=os.getenv('AWS_REGION', 'us-west-2'))
    # Prisma client for database operations
    prisma = Prisma()
except Exception as e:
    logger.error(f"Failed to initialize one or more clients: {e}")

# Startup event to connect Prisma
@app.on_event("startup")
async def startup():
    try:
        await prisma.connect()
        logger.info("Prisma client connected successfully")
    except Exception as e:
        logger.error(f"Failed to connect Prisma: {e}")

# Shutdown event to disconnect Prisma
@app.on_event("shutdown")
async def shutdown():
    try:
        await prisma.disconnect()
        logger.info("Prisma client disconnected")
    except Exception as e:
        logger.error(f"Error disconnecting Prisma: {e}")

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
    if not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(status_code=500, detail="OpenAI API Key not found on server.")

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
        
        # Post-processing: Calculate Experience Years
        processed_profiles = []
        for person in data:
            years = calculate_experience_years(person.get('experience', []))
            person['calculated_experience_years'] = years
            processed_profiles.append(person)

        # Optional: Store search query stats in DynamoDB here if needed
        
        return {
            "count": len(processed_profiles),
            "sql_generated": sql_query,
            "profiles": processed_profiles
        }
    except Exception as e:
        logger.error(f"Search error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

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

    run_input = {
        "urls": payload.linkedin_urls,
        "limitPerSource": payload.max_posts,
        "cookie": cookies_dict,
        "userAgent": payload.user_agent,
        "proxy": {"useApifyProxy": True}
    }

    try:
        # Create job record in database
        job = await prisma.scrapejob.create(
            data={
                "status": "PENDING",
                "linkedinUrls": payload.linkedin_urls,
                "maxPosts": payload.max_posts,
            }
        )
        job_id = job.id
        
        logger.info(f"Created job {job_id} for {len(payload.linkedin_urls)} URLs")
        
        # Start Apify actor without waiting (async)
        try:
            run = apify_client.actor(POST_SCRAPER_ACTOR_ID).start(run_input=run_input)
            apify_run_id = run['data']['id']
            
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
    try:
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
            run_status = run.get('data', {}).get('status')
            
            if run_status == 'SUCCEEDED':
                # Fetch results from Apify dataset
                dataset_id = run.get('data', {}).get('defaultDatasetId')
                if dataset_id:
                    dataset_items = list(apify_client.dataset(dataset_id).iterate_items())
                    
                    # Update job with results
                    await prisma.scrapejob.update(
                        where={"id": job_id},
                        data={
                            "status": "COMPLETED",
                            "result": {"posts_found": len(dataset_items), "data": dataset_items}
                        }
                    )
        
                    return {
                        "job_id": job_id,
                        "status": "COMPLETED",
                        "posts_found": len(dataset_items),
                        "data": dataset_items,
                        "created_at": job.createdAt.isoformat(),
                        "updated_at": datetime.now().isoformat()
                    }
                else:
                    return {
                        "job_id": job_id,
                        "status": "PROCESSING",
                        "message": "Run completed but dataset not available yet"
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