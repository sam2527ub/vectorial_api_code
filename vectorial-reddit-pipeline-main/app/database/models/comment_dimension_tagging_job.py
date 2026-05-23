"""CommentDimensionTaggingJob data model."""
from typing import Dict, Any, Optional


class CommentDimensionTaggingJob:
    """Represents a CommentDimensionTaggingJob record for tracking dimension tagging progress."""
    
    def __init__(self, row: Dict[str, Any]):
        self.id = row.get('id')
        self.audience_room_id = row.get('audienceRoomId')
        self.enterprise_name = row.get('enterpriseName')
        self.status = row.get('status', 'PENDING')
        self.total_comments = row.get('totalComments', 0)
        self.processed_comments = row.get('processedComments', 0)
        self.tagged_comments = row.get('taggedComments', 0)
        self.failed_comments = row.get('failedComments', 0)
        self.skipped_comments = row.get('skippedComments', 0)
        self.total_chunks = row.get('totalChunks', 0)
        self.completed_chunks = row.get('completedChunks', 0)
        self.chunk_size_profiles = row.get('chunkSizeProfiles')
        self.metadata = row.get('metadata')
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
            'totalComments': self.total_comments,
            'processedComments': self.processed_comments,
            'taggedComments': self.tagged_comments,
            'failedComments': self.failed_comments,
            'skippedComments': self.skipped_comments,
            'totalChunks': self.total_chunks,
            'completedChunks': self.completed_chunks,
            'chunkSizeProfiles': self.chunk_size_profiles,
            'metadata': self.metadata,
            'error': self.error,
            'result': self.result,
            'createdAt': self.created_at.isoformat() if self.created_at else None,
            'updatedAt': self.updated_at.isoformat() if self.updated_at else None,
            'startedAt': self.started_at.isoformat() if self.started_at else None,
            'completedAt': self.completed_at.isoformat() if self.completed_at else None,
        }
