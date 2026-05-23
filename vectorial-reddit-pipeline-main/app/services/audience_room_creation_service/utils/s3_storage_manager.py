"""S3 operations for audience room creation."""
from datetime import datetime
from typing import Dict, Any, List, Optional
from app.config import logger
from app.utils.s3_utils import (
    upload_json_to_s3,
    fetch_json_from_s3,
    extract_s3_key_from_url,
    ensure_enterprise_audience_folders_exist,
    get_s3_key_for_audience
)
from app.services.audience_room_creation_service.clients.storage.factory import get_storage_client


class S3Manager:
    """Manages S3 operations for audience room creation."""
    
    def __init__(self):
        """Initialize S3 manager."""
        self.storage_client = get_storage_client()
    
    def fetch_activities(
        self,
        activities: Optional[List[Dict[str, Any]]],
        activities_s3_url: Optional[str]
    ) -> List[Dict[str, Any]]:
        """Fetch activities from payload or S3."""
        if activities_s3_url:
            logger.info(f"Extracting S3 key from URL: {activities_s3_url}")
            s3_key = extract_s3_key_from_url(activities_s3_url)
            if s3_key:
                logger.info(f"Fetching JSON from S3 key: {s3_key}")
                activities = fetch_json_from_s3(
                    s3_key,
                    s3_client=self.storage_client.get_raw_client(),
                    s3_bucket=self.storage_client.get_bucket_name()
                )
                logger.info(f"Successfully fetched {len(activities)} activities from S3")
            else:
                raise ValueError("Invalid S3 URL for activities")
        
        return activities or []
    
    def upload_room_description(
        self,
        room_id: str,
        audience_room_name: str,
        audience_room_description: Optional[str],
        enterprise_name: str,
        source: str
    ) -> str:
        """Upload room description to S3."""
        description_key = get_s3_key_for_audience(
            room_id,
            "description.json",
            enterprise_name,
            source
        )
        description_data = {
            "audience_room_id": room_id,
            "audience_room_name": audience_room_name,
            "description": audience_room_description,
            "summary": None,
            "traits": None
        }
        return upload_json_to_s3(
            description_key,
            description_data,
            s3_client=self.storage_client.get_raw_client(),
            s3_bucket=self.storage_client.get_bucket_name(),
            s3_region=self.storage_client.get_region()
        )
    
    def upload_indexes(
        self,
        room_id: str,
        query: str,
        search_results: List[Dict[str, Any]],
        enterprise_name: str,
        source: str
    ) -> str:
        """Upload indexes to S3."""
        indexes_key = get_s3_key_for_audience(
            room_id,
            "indexes.json",
            enterprise_name,
            source
        )
        indexes_data = {
            "audience_room_id": room_id,
            "query": query,
            "total_results": len(search_results),
            "results": search_results,
            "timestamp": datetime.utcnow().isoformat()
        }
        return upload_json_to_s3(
            indexes_key,
            indexes_data,
            s3_client=self.storage_client.get_raw_client(),
            s3_bucket=self.storage_client.get_bucket_name(),
            s3_region=self.storage_client.get_region()
        )
    
    def upload_subreddit_similarity(
        self,
        room_id: str,
        subreddit_similarity_data: Dict[str, Any],
        enterprise_name: str,
        source: str
    ) -> str:
        similarity_key = get_s3_key_for_audience(
            room_id,
            "subreddit_similarity.json",
            enterprise_name,
            source
        )
        similarity_data = {
            "audience_room_id": room_id,
            "matched_subreddits": subreddit_similarity_data.get("matched_subreddits", []),
            "all_checked_subreddits": subreddit_similarity_data.get("all_checked_subreddits", []),
            "relevant_subreddits": subreddit_similarity_data.get("relevant_subreddits", []),
            "filtered_out_subreddits": subreddit_similarity_data.get("filtered_out_subreddits", []),
            "similarity_map": subreddit_similarity_data.get("similarity_map", {}),
            "total_checked_count": subreddit_similarity_data.get("total_checked_count", 0),
            "relevant_count": subreddit_similarity_data.get("relevant_count", 0),
            "filtered_out_count": subreddit_similarity_data.get("filtered_out_count", 0),
            "total_users_before": subreddit_similarity_data.get("total_users_before", 0),
            "total_users_after": subreddit_similarity_data.get("total_users_after", 0),
            "users_filtered_out": subreddit_similarity_data.get("users_filtered_out", 0),
            "users_passed": subreddit_similarity_data.get("users_passed", 0),

            "timestamp": datetime.utcnow().isoformat()
        }
        return upload_json_to_s3(
            similarity_key,
            similarity_data,
            s3_client=self.storage_client.get_raw_client(),
            s3_bucket=self.storage_client.get_bucket_name(),
            s3_region=self.storage_client.get_region()
        )
    
    def ensure_enterprise_folders(self, enterprise_name: str) -> None:
        """Ensure enterprise folders exist in S3."""
        ensure_enterprise_audience_folders_exist(
            enterprise_name,
            s3_client=self.storage_client.get_raw_client(),
            s3_bucket=self.storage_client.get_bucket_name()
        )
