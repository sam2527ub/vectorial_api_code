"""Profile summary processing - main entry point for fetching data and generating summaries."""
from typing import Dict, Any, Any as AnyType
from fastapi import HTTPException
from app.config import logger
from app.database.repositories.shared_repositories import update_audience_profile
from app.utils.s3_utils import upload_json_to_s3, extract_s3_key_from_url
from app.services.user_profile_summarization_service.clients.storage.factory import get_storage_client
from app.services.user_profile_summarization_service.summary_coordinator import ProfileSummaryGenerator
from app.services.user_profile_summarization_service.utils.s3_data_fetcher import S3DataFetcher
from app.services.user_profile_summarization_service.utils.result_builder import ResultBuilder


class ProfileProcessor:
    """Processes Reddit profiles: fetches data, generates summaries, updates S3."""
    
    def __init__(self):
        """Initialize profile processor."""
        self.summary_generator = ProfileSummaryGenerator()
        self.s3_fetcher = S3DataFetcher()
        self.result_builder = ResultBuilder()
        self.storage_client = get_storage_client()
    
    async def process_profile(
        self,
        profile: AnyType,
        audience_room_id: str
    ) -> Dict[str, Any]:
        """
        Process a single Reddit profile: fetch posts and comments, generate summary, update S3.
        """
        profile_id = profile.id
        profile_name = profile.profileName
        
        try:
            # Validate and fetch profile description
            profile_data = self.s3_fetcher.fetch_profile_description(profile, profile_id)
            if not profile_data:
                return self.result_builder.create_result(
                    profile_id, profile_name, "skipped", "no_description_url"
                )
            
            # Fetch activities (posts and comments)
            activities = self.s3_fetcher.fetch_activities(profile, profile_id)
            if not activities:
                return self.result_builder.create_result(
                    profile_id, profile_name, "skipped", "no_activities"
                )
            
            logger.info(
                f"Profile {profile_id}: Processing {len(activities)} activities "
                f"({self._count_posts(activities)} posts, {self._count_comments(activities)} comments)"
            )
            
            # Generate summary
            summary_result = await self.summary_generator.generate_from_activities(
                profile_id=profile_id,
                profile_name=profile_name,
                activities=activities
            )
            
            # Validate summary
            if not summary_result.get("summary") or not summary_result["summary"].strip():
                logger.error(f"Profile {profile_id} ({profile_name}): Empty summary result")
                return self.result_builder.create_result(
                    profile_id, profile_name, "error", "empty_summary",
                    "OpenAI API call failed or returned empty summary"
                )
            
            # Update profile data and upload to S3
            profile_key = extract_s3_key_from_url(profile.profileDescriptionS3Url)
            profile_data["summary"] = summary_result["summary"]
            profile_data["highlights"] = summary_result["highlights"]
            profile_data["keywords"] = summary_result["keywords"]
            
            updated_profile_url = upload_json_to_s3(
                profile_key,
                profile_data,
                s3_client=self.storage_client.get_raw_client(),
                s3_bucket=self.storage_client.get_bucket_name(),
                s3_region=self.storage_client.get_region()
            )
            update_audience_profile(
                profile_id,
                {"profileDescriptionS3Url": updated_profile_url}
            )
            
            return self.result_builder.create_success_result(
                profile_id, profile_name, summary_result
            )
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error processing profile {profile_id}: {e}", exc_info=True)
            return self.result_builder.create_result(
                profile_id, profile_name, "error", "processing_failed", str(e)
            )
    
    def _count_posts(self, activities: list) -> int:
        """Count posts in activities (heuristic: posts usually have 'title')."""
        return sum(1 for a in activities if a.get("title") or a.get("postTitle"))
    
    def _count_comments(self, activities: list) -> int:
        """Count comments in activities (heuristic: comments usually don't have 'title')."""
        return sum(1 for a in activities if not (a.get("title") or a.get("postTitle")))
