"""Configuration and client initialization."""
import os
import logging
from typing import Optional, TYPE_CHECKING

from dotenv import load_dotenv

# Load .env before any app import that reads os.environ (e.g. DATABASE_URL, AUDIENCE_BUCKET_NAME).
load_dotenv()

from app.runtime_settings import get_runtime_settings

_rs = get_runtime_settings()

_log_level = getattr(logging, _rs.logging.level.upper(), logging.INFO)
if not isinstance(_log_level, int):
    _log_level = logging.INFO
logging.basicConfig(level=_log_level)
logger = logging.getLogger(__name__)

from apify_client import ApifyClient
from openai import OpenAI
import boto3
from groq import Groq
from app import database

if TYPE_CHECKING:
    from peopledatalabs import PDLPY
    from anthropic import Anthropic

# Constants (defaults from ``config/runtime.yaml``; override scrapers/models there)
POST_SCRAPER_ACTOR_ID = _rs.scrapers.linkedin_post_actor_id
PROFILE_SCRAPER_ACTOR_ID = _rs.scrapers.linkedin_profile_actor_id
LINKEDIN_COMMENTS_ACTOR_ID = _rs.scrapers.linkedin_comments_actor_id

# Initialize Clients (Global variables - will be set during initialization)
pdl_client: Optional["PDLPY"] = None
apify_client: Optional[ApifyClient] = None
openai_client: Optional[OpenAI] = None
groq_client: Optional[Groq] = None
anthropic_client: Optional["Anthropic"] = None
dynamodb_resource = None
s3_client = None
s3_bucket = os.getenv("AUDIENCE_BUCKET_NAME") or os.getenv("VECTOR_BUCKET_NAME")
s3_region = os.getenv("AWS_REGION") or _rs.aws.region_default

# Database availability flags
main_db_available = database.is_main_db_available()
audience_db_available = database.is_audience_db_available()
logger.info(f"Main DB available: {main_db_available}, Audience DB available: {audience_db_available}")


def initialize_clients():
    """Initialize all third-party clients."""
    global pdl_client, apify_client, openai_client, groq_client, anthropic_client, dynamodb_resource, s3_client, s3_bucket
    
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
        groq_model = os.getenv("GROQ_MODEL") or _rs.api_clients.groq_default_model
        if groq_api_key:
            groq_client = Groq(api_key=groq_api_key)
            logger.info(f"Groq client initialized successfully with model: {groq_model}")
        else:
            logger.warning("GROQ_API_KEY not set")
    except Exception as e:
        logger.error(f"Failed to initialize Groq client: {e}")
        groq_client = None
    
    # Initialize Anthropic (Claude) client
    try:
        from anthropic import Anthropic
        anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
        if anthropic_api_key:
            anthropic_client = Anthropic(api_key=anthropic_api_key)
            logger.info("Anthropic (Claude) client initialized successfully")
        else:
            logger.warning("ANTHROPIC_API_KEY not set")
    except ImportError:
        logger.warning("anthropic package not installed. Install with: pip install anthropic")
        anthropic_client = None
    except Exception as e:
        logger.error(f"Failed to initialize Anthropic client: {e}")
        anthropic_client = None
    
    # Initialize DynamoDB (optional)
    try:
        dynamodb_resource = boto3.resource(
            "dynamodb", region_name=os.getenv("AWS_REGION") or _rs.aws.region_default
        )
    except Exception as e:
        logger.warning(f"DynamoDB not initialized: {e}")
    
    # Initialize S3 client (re-read bucket from env so reload / late .env edits match client)
    try:
        s3_bucket = os.getenv("AUDIENCE_BUCKET_NAME") or os.getenv("VECTOR_BUCKET_NAME")
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

