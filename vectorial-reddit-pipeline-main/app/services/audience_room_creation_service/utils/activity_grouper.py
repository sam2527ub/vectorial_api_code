"""Activity grouping utilities."""
from typing import Dict, List, Any
from app.services.audience_room_creation_service.utils.username_extractor import UsernameExtractor


class ActivityGrouper:
    """Groups activities by username."""
    
    def __init__(self):
        """Initialize activity grouper."""
        self.username_extractor = UsernameExtractor()
    
    def group_by_username(self, activities: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        """Group activities by username."""
        user_activities_map: Dict[str, List[Dict[str, Any]]] = {}
        
        for activity in activities:
            username = self.username_extractor.extract_from_activity(activity)
            if username:
                if username not in user_activities_map:
                    user_activities_map[username] = []
                user_activities_map[username].append(activity)
        
        return user_activities_map
