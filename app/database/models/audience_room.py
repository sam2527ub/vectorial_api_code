"""AudienceRoom data model."""
from typing import Dict, Any, List, TYPE_CHECKING

if TYPE_CHECKING:
    from app.database.models.audience_profile import AudienceProfile


class AudienceRoom:
    """Represents an AudienceRoom record."""
    def __init__(self, row: Dict[str, Any]):
        self.id = row.get('id')
        self.name = row.get('name')
        self.descriptionS3Url = row.get('descriptionS3Url') or row.get('descriptions3url')
        self.source = row.get('source')
        self.query = row.get('query')
        self.indexesS3Url = row.get('indexesS3Url') or row.get('indexess3url')
        self.userId = row.get('userId') or row.get('userid')
        self.createdAt = row.get('createdAt') or row.get('createdat')
        self.updatedAt = row.get('updatedAt') or row.get('updatedat')
        self.profiles: List['AudienceProfile'] = []
