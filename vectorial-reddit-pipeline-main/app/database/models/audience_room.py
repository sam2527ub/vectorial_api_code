"""AudienceRoom data model."""
from typing import Dict, Any, List, Optional


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
        self.category = row.get('category') or row.get('Category')  # b2b/b2c, existing DB column
        # Preview-then-Real workflow tracking columns (nullable; auto-provisioned
        # on first access via ensure_audience_room_preview_columns_exist).
        # Fall back to None when the columns don't exist yet.
        self.status: Optional[str] = row.get('status')
        self.isPreview: Optional[bool] = row.get('isPreview') or row.get('ispreview')
        self.previewReadyAt = row.get('previewReadyAt') or row.get('previewreadyat')
        self.fullReadyAt = row.get('fullReadyAt') or row.get('fullreadyat')
        self.profiles: List['AudienceProfile'] = []
