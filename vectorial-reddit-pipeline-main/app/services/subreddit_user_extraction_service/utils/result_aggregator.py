"""Result aggregation utilities."""
from typing import Dict, Any, List
from datetime import datetime


class ResultAggregator:
    """Aggregates scraping results from multiple subreddits."""
    
    @staticmethod
    def aggregate_subreddit_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Aggregate results from multiple subreddit scraping operations."""
        all_users: List[Dict[str, Any]] = []
        subreddit_results: List[Dict[str, Any]] = []
        successful_count = 0
        partial_count = 0
        failed_count = 0
        
        for result in results:
            subreddit = result["subreddit"]
            status = result.get("status", "error")
            
            if status in ["success", "partial"] and result.get("users"):
                users = result["users"]
                all_users.extend(users)
                if status == "success":
                    successful_count += 1
                else:
                    partial_count += 1
                subreddit_results.append({
                    "subreddit": subreddit,
                    "users_count": len(users),
                    "status": status,
                    "estimated_cost": result.get("estimated_cost"),
                    "aborted": result.get("aborted", False),
                    "abort_reason": result.get("abort_reason")
                })
            else:
                failed_count += 1
                subreddit_results.append({
                    "subreddit": subreddit,
                    "users_count": 0,
                    "status": "error",
                    "error": result.get("error", "Unknown error")
                })
        
        return {
            "total_subreddits": len(results),
            "successful_subreddits": successful_count,
            "failed_subreddits": failed_count,
            "total_users": len(all_users),
            "subreddit_results": subreddit_results,
            "all_users": all_users,
            "completed_at": datetime.now().isoformat()
        }
