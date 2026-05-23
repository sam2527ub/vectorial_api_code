"""SubredditSimilarityFilterJob data model."""
from typing import Dict, Any


class SubredditSimilarityFilterJob:
    """Represents a SubredditSimilarityFilterJob record for tracking similarity filter progress."""

    def __init__(self, row: Dict[str, Any]):
        self.id = row.get('id')
        self.status = row.get('status', 'PENDING')
        self.enterprise_name = row.get('enterpriseName')
        self.total_subreddits = row.get('totalSubreddits', 0)
        self.checked_subreddits = row.get('checkedSubreddits', 0)
        self.total_users_before = row.get('totalUsersBefore', 0)
        self.total_users_after = row.get('totalUsersAfter', 0)
        self.error = row.get('error')
        self.result = row.get('result')
        self.input_s3_key = row.get('inputS3Key')
        self.result_s3_key = row.get('resultS3Key')
        self.created_at = row.get('createdAt')
        self.updated_at = row.get('updatedAt')
        self.started_at = row.get('startedAt')
        self.completed_at = row.get('completedAt')

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            'id': self.id,
            'status': self.status,
            'enterpriseName': self.enterprise_name,
            'totalSubreddits': self.total_subreddits,
            'checkedSubreddits': self.checked_subreddits,
            'totalUsersBefore': self.total_users_before,
            'totalUsersAfter': self.total_users_after,
            'error': self.error,
            'result': self.result,
            'inputS3Key': self.input_s3_key,
            'resultS3Key': self.result_s3_key,
            'createdAt': self.created_at.isoformat() if self.created_at else None,
            'updatedAt': self.updated_at.isoformat() if self.updated_at else None,
            'startedAt': self.started_at.isoformat() if self.started_at else None,
            'completedAt': self.completed_at.isoformat() if self.completed_at else None,
        }
