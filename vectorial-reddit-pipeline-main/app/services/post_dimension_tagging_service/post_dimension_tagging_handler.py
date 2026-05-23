"""Post dimension tagging handler."""
import asyncio
from typing import Dict, Any, Optional
from app.config import logger
from .config import PostDimensionTaggingConfig
from .clients.storage.factory import get_storage_client
from .repositories.audience_room_repository import find_audience_room_with_category
from .utils.category_selector import get_category_and_dimensions
from .utils.post_loader import load_posts_from_profiles
from .utils.s3_updater import update_posts_in_s3
from .dimension_tagging_processor import DimensionTaggingProcessor
from .chunk_processor import ChunkProcessor
from .job_status_manager import JobStatusManager


class PostDimensionTaggingHandler:
    """Handler for tagging posts with dimensions."""

    def __init__(self):
        """Initialize handler."""
        self.config = PostDimensionTaggingConfig()
        self.dimension_processor = DimensionTaggingProcessor()
        self.storage_client = get_storage_client()
        self.chunk_processor = ChunkProcessor(
            self.dimension_processor, self.storage_client, self.config
        )
    
    async def tag_posts_for_audience_room(
        self,
        audience_room_id: str,
        enterprise_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Tag all posts in an audience room with dimensions.
        
        Args:
            audience_room_id: ID of the audience room
            enterprise_name: Enterprise name for database routing
            
        Returns:
            Dict with status, counts, and updated S3 URLs
        """
        if not self.storage_client or not self.storage_client.is_configured():
            raise ValueError("Storage client not configured")
        
        # Fetch audience room with category
        audience_room = await find_audience_room_with_category(
            audience_room_id,
            enterprise_name=enterprise_name,
            include_profiles=True
        )
        
        if not audience_room:
            raise ValueError(f"Audience room {audience_room_id} not found")
        
        # Determine category and get dimensions/prompt template
        category_lower, dimensions, trait_titles, prompt_template = get_category_and_dimensions(audience_room)
        
        logger.debug(
            f"Tagging posts for audience room {audience_room_id} "
            f"(category: {category_lower}, dimensions: {len(dimensions)})"
        )
        
        # Load posts
        all_posts, profile_posts_map = await load_posts_from_profiles(self.storage_client, audience_room.profiles)
        if not all_posts:
            return {
                "status": "success", "message": "No posts found to tag",
                "total_posts": 0, "tagged_posts": 0, "failed_posts": 0, "updated_s3_urls": []
            }
        
        total_posts = len(all_posts)
        if getattr(self, "_current_job_id", None):
            JobStatusManager.update_total_posts(self._current_job_id, total_posts, enterprise_name)
        
        # Process posts (AI gateway called inside processor)
        tagging_results = await asyncio.to_thread(
            self.dimension_processor.tag_dimensions,
            all_posts,
            dimensions,
            trait_titles,
            prompt_template,
            self.config,
        )
        
        # Add dimensions to posts
        tagged_posts = 0
        for result in tagging_results:
            result["post"]["dimensions"] = result["dimensions"]
            result["post"]["traits"] = result.get("traits", {})
            result["post"]["usefulness_score"] = result.get("usefulness_score", 0.0)
            tagged_posts += 1
        
        # Update S3
        await update_posts_in_s3(self.storage_client, profile_posts_map)
        
        return {
            "status": "success", "total_posts": total_posts,
            "tagged_posts": tagged_posts, "failed_posts": total_posts - tagged_posts,
            "updated_s3_urls": []
        }
    
    async def process_job_chunk(
        self, job_id: str, audience_room_id: str, enterprise_name: Optional[str],
        start_profile_index: int, base_url: str, chunk_size: int
    ) -> None:
        """Process one chunk of profiles."""
        await self.chunk_processor.process_chunk(
            job_id, audience_room_id, enterprise_name, start_profile_index, chunk_size, base_url
        )
    
    async def process_job_async(
        self, job_id: str, audience_room_id: str, enterprise_name: Optional[str] = None
    ) -> None:
        """Process a dimension tagging job asynchronously."""
        try:
            self._current_job_id = job_id
            JobStatusManager.mark_job_processing(job_id, enterprise_name)
            
            result = await self.tag_posts_for_audience_room(audience_room_id, enterprise_name)
            total_posts = result.get('total_posts', 0)
            
            JobStatusManager.complete_job(
                job_id=job_id, result=result,
                tagged_posts=result.get('tagged_posts', 0),
                failed_posts=result.get('failed_posts', 0),
                total_posts=total_posts, processed_posts=total_posts,
                enterprise_name=enterprise_name
            )
        except Exception as e:
            JobStatusManager.fail_job(job_id, error=str(e), enterprise_name=enterprise_name)
        finally:
            if hasattr(self, '_current_job_id'):
                del self._current_job_id
