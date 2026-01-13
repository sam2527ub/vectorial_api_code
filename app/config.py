"""Configuration and client initialization."""
import os
import logging
from typing import Optional, TYPE_CHECKING
from dotenv import load_dotenv
from apify_client import ApifyClient
from openai import OpenAI
import boto3
from groq import Groq
from app import database

if TYPE_CHECKING:
    from peopledatalabs import PDLPY

# Load environment variables
load_dotenv()

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
POST_SCRAPER_ACTOR_ID = "curious_coder/linkedin-post-search-scraper"
PROFILE_SCRAPER_ACTOR_ID = "2SyF0bVxmgGr8IVCZ"  # LinkedIn Profile Scraper

# Initialize Clients (Global variables - will be set during initialization)
pdl_client: Optional["PDLPY"] = None
apify_client: Optional[ApifyClient] = None
openai_client: Optional[OpenAI] = None
groq_client: Optional[Groq] = None
dynamodb_resource = None
s3_client = None
s3_bucket = os.getenv("AUDIENCE_BUCKET_NAME") or os.getenv("VECTOR_BUCKET_NAME")
s3_region = os.getenv("AWS_REGION", "us-west-2")

# Database availability flags
main_db_available = database.is_main_db_available()
audience_db_available = database.is_audience_db_available()
logger.info(f"Main DB available: {main_db_available}, Audience DB available: {audience_db_available}")


def initialize_clients():
    """Initialize all third-party clients."""
    global pdl_client, apify_client, openai_client, groq_client, dynamodb_resource, s3_client
    
    # Initialize PDL client (lazy import to avoid Pydantic compatibility issues)
    try:
        from peopledatalabs import PDLPY
        pdl_client = PDLPY(api_key=os.getenv("PDL_API_KEY"))
    except (ImportError, TypeError, Exception) as e:
        logger.error(f"Failed to initialize PDL client: {e}")
        logger.warning("PDL client will not be available. This may be due to Pydantic version incompatibility.")
        pdl_client = None
    
    # Initialize Apify client
    try:
        apify_client = ApifyClient(os.getenv("APIFY_API_TOKEN"))
    except Exception as e:
        logger.error(f"Failed to initialize Apify client: {e}")
    
    # Initialize OpenAI client
    try:
        openai_api_key = os.getenv("OPENAI_API_KEY")
        if openai_api_key:
            openai_client = OpenAI(api_key=openai_api_key)
        else:
            logger.warning("OPENAI_API_KEY not set")
    except Exception as e:
        logger.error(f"Failed to initialize OpenAI client: {e}")
        openai_client = None
    
    # Initialize Groq client
    try:
        groq_api_key = os.getenv("GROQ_API_KEY")
        groq_model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
        if groq_api_key:
            groq_client = Groq(api_key=groq_api_key)
            logger.info(f"Groq client initialized successfully with model: {groq_model}")
        else:
            logger.warning("GROQ_API_KEY not set")
    except Exception as e:
        logger.error(f"Failed to initialize Groq client: {e}")
        groq_client = None
    
    # Initialize DynamoDB (optional)
    try:
        dynamodb_resource = boto3.resource('dynamodb', region_name=os.getenv('AWS_REGION', 'us-west-2'))
    except Exception as e:
        logger.warning(f"DynamoDB not initialized: {e}")
    
    # Initialize S3 client
    try:
        s3_client = boto3.client("s3", region_name=s3_region) if s3_bucket else None
        if s3_client and s3_bucket:
            logger.info(f"S3 client initialized for bucket {s3_bucket}")
        else:
            logger.warning("S3 bucket not configured; audience uploads will be disabled")
    except Exception as e:
        logger.error(f"Failed to initialize S3 client: {e}")
        s3_client = None


# Initialize clients on module import
initialize_clients()

