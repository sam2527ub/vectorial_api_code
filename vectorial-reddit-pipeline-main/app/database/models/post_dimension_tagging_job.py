"""PostDimensionTaggingJob data model."""
from typing import Dict, Any, Optional


class PostDimensionTaggingJob:
    """Represents a PostDimensionTaggingJob record for tracking dimension tagging progress."""
    
    def __init__(self, row: Dict[str, Any]):
        self.id = row.get('id')
        self.audience_room_id = row.get('audienceRoomId')
        self.enterprise_name = row.get('enterpriseName')
        self.status = row.get('status', 'PENDING')
        self.total_posts = row.get('totalPosts', 0)
        self.processed_posts = row.get('processedPosts', 0)
        self.tagged_posts = row.get('taggedPosts', 0)
        self.failed_posts = row.get('failedPosts', 0)
        self.error = row.get('error')
        self.result = row.get('result')
        self.created_at = row.get('createdAt')
        self.updated_at = row.get('updatedAt')
        self.started_at = row.get('startedAt')
        self.completed_at = row.get('completedAt')
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            'id': self.id,
            'audienceRoomId': self.audience_room_id,
            'enterpriseName': self.enterprise_name,
            'status': self.status,
            'totalPosts': self.total_posts,
            'processedPosts': self.processed_posts,
            'taggedPosts': self.tagged_posts,
            'failedPosts': self.failed_posts,
            'error': self.error,
            'result': self.result,
            'createdAt': self.created_at.isoformat() if self.created_at else None,
            'updatedAt': self.updated_at.isoformat() if self.updated_at else None,
            'startedAt': self.started_at.isoformat() if self.started_at else None,
            'completedAt': self.completed_at.isoformat() if self.completed_at else None,
        }
