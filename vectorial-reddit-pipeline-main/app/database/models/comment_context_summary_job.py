"""CommentContextSummaryJob model."""
from typing import Optional, Dict, Any
from datetime import datetime


class CommentContextSummaryJob:
    """Model for comment context summary job."""

    def __init__(self, row: Optional[Dict[str, Any]]):
        if not row:
            raise ValueError("Row cannot be None")
        self.id = row.get("id")
        self.audience_room_id = row.get("audienceRoomId")
        self.enterprise_name = row.get("enterpriseName")
        self.status = row.get("status", "PENDING")
        self.total_profiles = row.get("totalProfiles", 0)
        self.processed_profiles = row.get("processedProfiles", 0)
        self.total_comments = row.get("totalComments", 0)
        self.enriched_comments = row.get("enrichedComments", 0)
        self.skipped_comments = row.get("skippedComments", 0)
        self.failed_comments = row.get("failedComments", 0)
        self.error = row.get("error")
        self.result = row.get("result")
        self.created_at = row.get("createdAt")
        self.updated_at = row.get("updatedAt")
        self.started_at = row.get("startedAt")
        self.completed_at = row.get("completedAt")
