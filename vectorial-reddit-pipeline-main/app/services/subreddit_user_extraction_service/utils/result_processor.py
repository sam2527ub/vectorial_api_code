"""Result processing for subreddit user extraction."""
from typing import Dict, Any, List, Optional
from datetime import datetime
from app.config import logger
from app.services.subreddit_user_extraction_service.utils.result_aggregator import ResultAggregator
from app.services.subreddit_user_extraction_service.utils.user_filter import UserFilter
from app.services.subreddit_user_extraction_service.config import ExtractionConfig


class ResultProcessor:
    """Processes and aggregates scraping results."""
    
    def __init__(self, config: Optional[ExtractionConfig] = None):
        """Initialize result processor."""
        self.config = config or ExtractionConfig()
        self.aggregator = ResultAggregator()
        self.user_filter = UserFilter()
    
    def process_results(
        self,
        results: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Process and aggregate results from multiple subreddits."""
        # Aggregate results
        aggregated = self.aggregator.aggregate_subreddit_results(results)
        
        all_users = aggregated["all_users"]
        
        logger.info(
            f"Batch scraping completed: {aggregated['successful_subreddits']} successful, "
            f"{len([r for r in results if r.get('status') == 'partial'])} partial (aborted), "
            f"{aggregated['failed_subreddits']} failed, {len(all_users)} total users before filtering"
        )
        
        # Filter users by contribution
        filtered_users = self.user_filter.filter_by_contribution(
            all_users,
            self.config.min_contribution
        )
        
        logger.info(
            f"Filtered to {len(filtered_users)} users with contribution >= {self.config.min_contribution} "
            f"out of {len(all_users)} total users"
        )
        
        return {
            "total_subreddits": aggregated["total_subreddits"],
            "successful_subreddits": aggregated["successful_subreddits"],
            "failed_subreddits": aggregated["failed_subreddits"],
            "total_users": len(all_users),
            "filtered_users_count": len(filtered_users),
            "subreddits": aggregated["subreddit_results"],
            "users": filtered_users,
            "completed_at": datetime.now().isoformat()
        }
