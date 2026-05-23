"""ParallelSearchJob data model."""
from typing import Dict, Any, List


class ParallelSearchJob:
    """Data model for Parallel API search jobs stored in database."""
    
    def __init__(self, row: Dict[str, Any]):
        self.id = row.get('id')
        self.run_id = row.get('run_id')
        self.query = row.get('query')
        self.model = row.get('model')
        self.match_limit = row.get('match_limit')
        self.status = row.get('status')  # 'running', 'completed', 'failed'
        self.subreddits = row.get('subreddits') or []
        self.error = row.get('error')
        self.created_at = row.get('created_at')
        self.updated_at = row.get('updated_at')
        self.completed_at = row.get('completed_at')
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            'id': self.id,
            'run_id': self.run_id,
            'query': self.query,
            'model': self.model,
            'match_limit': self.match_limit,
            'status': self.status,
            'subreddits': self.subreddits,
            'error': self.error,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
        }
