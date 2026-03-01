"""ScrapeJob data model."""
from typing import Dict, Any


class ScrapeJob:
    """Represents a ScrapeJob record."""
    def __init__(self, row: Dict[str, Any]):
        self.id = row.get('id')
        self.status = row.get('status', 'PENDING')
        self.linkedinUrls = row.get('linkedinUrls') or row.get('linkedinurls')
        self.maxPosts = row.get('maxPosts') or row.get('maxposts')
        self.apifyRunId = row.get('apifyRunId') or row.get('apifyrunid')
        self.result = row.get('result')
        self.error = row.get('error')
        self.audienceRoomId = row.get('audienceRoomId') or row.get('audienceroomid')
        self.createdAt = row.get('createdAt') or row.get('createdat')
        self.updatedAt = row.get('updatedAt') or row.get('updatedat')
