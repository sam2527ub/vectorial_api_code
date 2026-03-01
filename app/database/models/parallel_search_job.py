"""ParallelSearchJob data model."""
from typing import Dict, Any


class ParallelSearchJob:
    """Represents a ParallelSearchJob record."""
    def __init__(self, row: Dict[str, Any]):
        self.id = row.get('id')
        self.status = row.get('status', 'PENDING')
        self.query = row.get('query')
        self.model = row.get('model', 'core')
        self.matchLimit = row.get('matchLimit') or row.get('matchlimit')
        self.parallelRunId = row.get('parallelRunId') or row.get('parallelrunid')
        self.profiles = row.get('profiles')
        self.result = row.get('result')
        self.error = row.get('error')
        self.enterpriseName = row.get('enterpriseName') or row.get('enterprisename')
        self.createdAt = row.get('createdAt') or row.get('createdat')
        self.updatedAt = row.get('updatedAt') or row.get('updatedat')
