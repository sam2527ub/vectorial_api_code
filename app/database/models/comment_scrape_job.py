"""CommentScrapeJob data model."""
from typing import Dict, Any


class CommentScrapeJob:
    """Represents a CommentScrapeJob record."""
    def __init__(self, row: Dict[str, Any]):
        self.id = row.get('id')
        self.audienceRoomId = row.get('audienceRoomId') or row.get('audienceroomid')
        self.enterpriseName = row.get('enterpriseName') or row.get('enterprisename')
        self.status = row.get('status', 'PROCESSING')
        self.result = row.get('result')
        self.error = row.get('error')
        self.createdAt = row.get('createdAt') or row.get('createdat')
        self.updatedAt = row.get('updatedAt') or row.get('updatedat')
