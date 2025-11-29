import os
import json
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime
from dateutil.parser import parse, ParserError

# FastAPI Imports
from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Third-party Clients
from dotenv import load_dotenv
from peopledatalabs import PDLPY
from apify_client import ApifyClient
from openai import OpenAI
import boto3

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
except Exception as e:
    logger.error(f"Failed to initialize one or more clients: {e}")

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

class ScrapeRequest(BaseModel):
    linkedin_urls: List[str]
    max_posts: int = 25
    cookies: Optional[List[Dict]] = None 
    user_agent: Optional[str] = None

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

# === STEP 4: TRIGGER POST SCRAPER ===
@app.post("/api/v1/scrape")
async def trigger_scraping(payload: ScrapeRequest):
    """
    Triggers Apify Actor (linkedin-post-search-scraper).
    Requires a valid list of LinkedIn URLs and Cookies.
    """
    if not payload.cookies or not payload.user_agent:
        raise HTTPException(status_code=400, detail="Cookies and User Agent are required for scraping.")

    run_input = {
        "urls": payload.linkedin_urls,
        "limitPerSource": payload.max_posts,
        "cookie": payload.cookies,
        "userAgent": payload.user_agent,
        "proxy": {"useApifyProxy": True}
    }

    try:
        logger.info(f"Starting Apify scrape for {len(payload.linkedin_urls)} URLs")
        # Start the actor
        run = apify_client.actor(POST_SCRAPER_ACTOR_ID).call(run_input=run_input)
        
        # Fetch results from dataset
        dataset_items = list(apify_client.dataset(run['defaultDatasetId']).iterate_items())
        
        return {
            "status": "success",
            "run_id": run['id'],
            "posts_found": len(dataset_items),
            "data": dataset_items
        }
    except Exception as e:
        logger.error(f"Apify error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)