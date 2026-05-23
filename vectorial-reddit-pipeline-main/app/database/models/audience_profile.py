"""AudienceProfile data model."""
from typing import Dict, Any, Optional


class AudienceProfile:
    """Represents an AudienceProfile record."""
    def __init__(self, row: Dict[str, Any]):
        self.id = row.get('id')
        self.audienceRoomId = row.get('audienceRoomId') or row.get('audienceroomid')
        self.profileName = row.get('profileName') or row.get('profilename')
        # Support both old and new column names for backward compatibility
        self.profileUrl = row.get('profileUrl') or row.get('profileurl') or row.get('linkedinUrl') or row.get('linkedinurl')
        self.profileDescriptionS3Url = row.get('profileDescriptionS3Url') or row.get('profiledescriptions3url')
        self.postsS3Url = row.get('postsS3Url') or row.get('postss3url')
        self.commentsS3Url = row.get('commentsS3Url') or row.get('commentss3url')
        self.source = row.get('source')
        self.createdAt = row.get('createdAt') or row.get('createdat')
        self.updatedAt = row.get('updatedAt') or row.get('updatedat')
        self.audienceRoom: Optional['AudienceRoom'] = None
