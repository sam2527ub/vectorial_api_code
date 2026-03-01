"""PostClassifier data model."""
import json
from typing import Dict, Any, List


class PostClassifier:
    """Represents a PostClassifier record."""
    def __init__(self, row: Dict[str, Any]):
        self.id = row.get('id')
        self.userId = row.get('userId') or row.get('userid')
        self.name = row.get('name')
        self.description = row.get('description')
        self.prompt = row.get('prompt')
        labels_raw = row.get('labels')
        if isinstance(labels_raw, str):
            try:
                self.labels = json.loads(labels_raw)
            except json.JSONDecodeError:
                self.labels = [labels_raw] if labels_raw else []
        elif isinstance(labels_raw, list):
            self.labels = labels_raw
        else:
            self.labels = []
        examples_raw = row.get('examples')
        if isinstance(examples_raw, str):
            try:
                self.examples = json.loads(examples_raw)
            except json.JSONDecodeError:
                self.examples = None
        else:
            self.examples = examples_raw
        self.createdAt = row.get('createdAt') or row.get('createdat')
        self.updatedAt = row.get('updatedAt') or row.get('updatedat')
