"""Comment dimension tagging handler."""
import math
from typing import Dict, Any, Optional
from app.config import logger
from .config import CommentDimensionTaggingConfig
from .clients.storage import get_storage_client
from .dimension_tagging_processor import DimensionTaggingProcessor
from .chunk_processor import ChunkProcessor
from .repositories.audience_room_repository import find_audience_room_with_category


class CommentDimensionTaggingHandler:
    """Handler for tagging comments with dimensions."""
    
    def __init__(self):
        """Initialize handler."""
        self.config = CommentDimensionTaggingConfig()
        self.dimension_processor = DimensionTaggingProcessor()
        self.storage_client = get_storage_client()
        self.chunk_processor = ChunkProcessor(
            self.dimension_processor, self.storage_client, self.config
        )
    
    def _validate(self) -> None:
        """Validate that required clients are configured."""
        if not self.storage_client or not self.storage_client.is_configured():
            raise ValueError("Storage client not configured")
    
    def calculate_chunk_size(self, total_profiles: int) -> int:
        """Calculate optimal chunk size based on profile count."""
        if total_profiles == 0:
            return self.config.chunk_size_profiles
        
        # Target ~600 comments per chunk
        estimated_comments = total_profiles * self.config.avg_comments_per_profile
        estimated_chunks = max(1, math.ceil(estimated_comments / self.config.target_comments_per_chunk))
        chunk_size = max(5, min(20, math.ceil(total_profiles / estimated_chunks)))
        
        return chunk_size
    
    async def process_job_chunk(
        self, job_id: str, audience_room_id: str, enterprise_name: Optional[str],
        start_profile_index: int, base_url: str, chunk_size: int
    ) -> None:
        """Process one chunk of profiles."""
        await self.chunk_processor.process_chunk(
            job_id, audience_room_id, enterprise_name, start_profile_index, chunk_size, base_url
        )
