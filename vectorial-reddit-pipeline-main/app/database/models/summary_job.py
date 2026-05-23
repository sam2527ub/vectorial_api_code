"""SummaryJob data model."""
from typing import Dict, Any


class SummaryJob:
    """Represents a SummaryJob record for tracking async summary generation."""
    def __init__(self, row: Dict[str, Any]):
        self.id = row.get('id')
        self.audienceRoomId = row.get('audienceRoomId') or row.get('audienceroomid')
        self.status = row.get('status', 'running')
        self.totalProfiles = row.get('totalProfiles') or row.get('totalprofiles', 0)
        self.processedProfiles = row.get('processedProfiles') or row.get('processedprofiles', 0)
        self.successCount = row.get('successCount') or row.get('successcount', 0)
        self.skippedCount = row.get('skippedCount') or row.get('skippedcount', 0)
        self.errorCount = row.get('errorCount') or row.get('errorcount', 0)
        self.profiles = row.get('profiles')  # JSON field
        self.error = row.get('error')
        self.startedAt = row.get('startedAt') or row.get('startedat')
        self.updatedAt = row.get('updatedAt') or row.get('updatedat')
        self.completedAt = row.get('completedAt') or row.get('completedat')
