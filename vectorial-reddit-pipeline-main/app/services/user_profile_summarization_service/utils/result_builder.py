"""Result building utilities for profile processing."""
from typing import Dict, Any


class ResultBuilder:
    """Builds result dictionaries for profile processing."""
    
    @staticmethod
    def create_result(
        profile_id: str,
        profile_name: str,
        status: str,
        reason: str,
        error: str = None
    ) -> Dict[str, Any]:
        """Create result dictionary."""
        return {
            "profile_id": profile_id,
            "profile_name": profile_name,
            "status": status,
            "reason": reason,
            "error": error
        }
    
    @staticmethod
    def create_success_result(
        profile_id: str,
        profile_name: str,
        summary_result: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Create success result dictionary."""
        summary_text = summary_result.get("summary", "")
        summary_preview = (
            summary_text[:100] + "..." 
            if summary_text and len(summary_text) > 100 
            else summary_text
        )
        
        return {
            "profile_id": profile_id,
            "profile_name": profile_name,
            "status": "success",
            "summary": summary_preview,
            "highlights_count": len(summary_result.get("highlights", [])),
            "keywords_count": len(summary_result.get("keywords", [])),
            "error": None
        }
    
    @staticmethod
    def empty_summary_result() -> Dict[str, Any]:
        """Return empty summary result structure."""
        return {
            "summary": None,
            "highlights": [],
            "keywords": []
        }
