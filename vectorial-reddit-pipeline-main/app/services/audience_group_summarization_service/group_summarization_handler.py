"""Request handler for group summary generation endpoint."""
from typing import Dict, Any, List, Optional
from fastapi import HTTPException
from app.config import logger
from app.services.audience_group_summarization_service.repositories import (
    find_audience_room_by_id,
    update_audience_room
)
from app.utils.s3_utils import upload_json_to_s3, extract_s3_key_from_url, fetch_json_from_s3
from app.services.audience_group_summarization_service.clients.storage.factory import get_storage_client
from app.services.audience_group_summarization_service.config import GroupSummaryConfig
from app.services.audience_group_summarization_service.utils.profile_summary_utils import ProfileSummaryFetcher
from app.services.audience_group_summarization_service.group_summary_generator import GroupSummaryGenerator
from app.services.audience_group_summarization_service.traits_generator import TraitsGenerator


class GroupSummarizationHandler:
    """Handles group summary generation requests."""
    
    def __init__(self):
        """Initialize request handler."""
        self.config = GroupSummaryConfig()
        self.summary_fetcher = ProfileSummaryFetcher()
        self.group_summary_generator = GroupSummaryGenerator()
        self.traits_generator = TraitsGenerator()
        self.storage_client = get_storage_client()
    
    async def handle_request(
        self,
        audience_room_id: str,
        enterprise_name: Optional[str],
        use_posts_and_about: bool = False,
    ) -> Dict[str, Any]:
        """
        Handle group summary generation request.

        When use_posts_and_about=True (Preview-then-Real workflow's preview
        pipeline), per-profile text is built from scraped posts.json and
        comments.json directly instead of requiring LLM-generated profile
        summaries. Reddit has no "about" equivalent so we lean on activity
        text only - the flag name is kept in sync with the LinkedIn API.
        """
        # Validate dependencies
        self.config.validate_dependencies()
        
        # Fetch audience room and profiles
        audience_room = find_audience_room_by_id(
            audience_room_id,
            include_profiles=True,
            enterprise_name=enterprise_name
        )
        if not audience_room:
            raise HTTPException(
                status_code=404,
                detail=f"Audience room {audience_room_id} not found"
            )
        
        profiles = audience_room.profiles
        if not profiles:
            raise HTTPException(
                status_code=404,
                detail=f"No profiles found in audience room {audience_room_id}"
            )
        
        # Fetch audience room description JSON from S3
        if not audience_room.descriptionS3Url:
            raise HTTPException(
                status_code=404,
                detail="Description not found for this audience room"
            )
        
        description_key = extract_s3_key_from_url(audience_room.descriptionS3Url)
        if not description_key:
            raise HTTPException(
                status_code=500,
                detail="Invalid S3 URL format for audience room description"
            )
        
        room_description_data = fetch_json_from_s3(
            description_key,
            s3_client=self.storage_client.get_raw_client(),
            s3_bucket=self.storage_client.get_bucket_name()
        )
        
        logger.info(
            f"Generating group summary for {len(profiles)} profiles "
            f"in audience room {audience_room_id}"
        )
        
        # Fetch profile summaries from S3. Preview pipeline takes the
        # posts+comments shortcut and never requires LLM profile summaries.
        if use_posts_and_about:
            profile_summaries, fetch_stats = self.summary_fetcher.fetch_preview_style_summaries(profiles)
            if not profile_summaries:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "No scraped posts or comments found on any profile. The preview "
                        "group summary needs at least one profile with posts.json or "
                        "comments.json uploaded."
                    )
                )
        else:
            profile_summaries, fetch_stats = self.summary_fetcher.fetch_summaries(profiles)
            if not profile_summaries:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "No profile summaries found. Please generate profile summaries first "
                        "using /api/v1/audience-rooms/{audience_room_id}/generate-summaries"
                    )
                )
        
        # Generate group summary
        try:
            group_summary = await self.group_summary_generator.generate(
                profile_summaries, audience_room_id
            )
        except Exception as e:
            logger.error(f"Error generating group summary: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to generate group summary: {str(e)}"
            )
        
        # Generate traits
        try:
            traits_list = await self.traits_generator.generate(
                profile_summaries, audience_room_id
            )
        except ValueError as e:
            logger.error(f"Invalid traits structure: {e}")
            raise HTTPException(status_code=500, detail=f"Invalid traits structure: {str(e)}")
        except Exception as e:
            logger.error(f"Error generating traits: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to generate traits: {str(e)}")
        
        # Update audience room description JSON with the summary and traits
        room_description_data["summary"] = group_summary
        room_description_data["traits"] = traits_list
        
        # Upload updated description back to S3
        updated_description_url = upload_json_to_s3(
            description_key,
            room_description_data,
            s3_client=self.storage_client.get_raw_client(),
            s3_bucket=self.storage_client.get_bucket_name(),
            s3_region=self.storage_client.get_region()
        )
        
        # Update the audience room record with the new URL
        update_audience_room(
            audience_room_id,
            {"descriptionS3Url": updated_description_url},
            enterprise_name=enterprise_name
        )
        
        response = {
            "audience_room_id": audience_room_id,
            "audience_room_name": audience_room.name,
            "summary": group_summary,
            "traits": traits_list,
            "total_profiles": len(profiles),
            "profiles_processed": fetch_stats["profiles_processed"],
            "profiles_skipped": fetch_stats["profiles_skipped"],
            "description_s3_url": updated_description_url
        }
        
        return response
