"""Configuration and client initialization."""
import os
import logging
from typing import Optional
from dotenv import load_dotenv


load_dotenv()

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
# Suppress noisy HTTP request logs from httpx/openai
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


openai_client = None
anthropic_client = None
s3_client = None
s3_bucket = os.getenv("AUDIENCE_BUCKET_NAME") or os.getenv("VECTOR_BUCKET_NAME") or "audience-room-uploads"
s3_region = os.getenv("AWS_REGION", "us-west-2")
