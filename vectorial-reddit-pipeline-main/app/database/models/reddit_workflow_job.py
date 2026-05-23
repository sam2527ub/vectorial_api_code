"""RedditWorkflowJob data model."""
from typing import Dict, Any


class RedditWorkflowJob:
    """Represents a RedditWorkflowJob record for tracking Reddit workflow progress."""
    def __init__(self, row: Dict[str, Any]):
        self.id = row.get('id')
        self.job_id = row.get('job_id')
        self.run_id = row.get('run_id')
        self.user_id = row.get('user_id')
        self.enterprise_name = row.get('enterprise_name')
        self.status = row.get('status', 'PENDING')
        self.current_step = row.get('current_step', 'initializing')
        self.progress_percentage = row.get('progress_percentage', 0)
        self.input_data = row.get('input_data')
        self.step_details = row.get('step_details') or {}
        self.error_message = row.get('error_message')
        self.created_at = row.get('created_at')
        self.updated_at = row.get('updated_at')
        self.completed_at = row.get('completed_at')
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            'id': self.id,
            'job_id': self.job_id,
            'run_id': self.run_id,
            'user_id': self.user_id,
            'enterprise_name': self.enterprise_name,
            'status': self.status,
            'current_step': self.current_step,
            'progress_percentage': self.progress_percentage,
            'input_data': self.input_data,
            'step_details': self.step_details,
            'error_message': self.error_message,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
        }
