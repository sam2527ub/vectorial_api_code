"""Request/Response schemas for subreddit user extraction."""
from typing import Optional, List
from pydantic import BaseModel, Field


class SubredditBatchExtractionRequest(BaseModel):
    """Request schema for batch subreddit user extraction."""
    subreddits: List[str] = Field(..., min_items=1, description="List of subreddit names to scrape (without r/ prefix)")
    max_posts: Optional[int] = Field(None, ge=1, description="Number of posts to analyze per subreddit. Leave empty to scrape all posts.")
    max_comments: Optional[int] = Field(None, ge=1, description="Number of comments to analyze per subreddit. Leave empty to scrape all comments.")
    fetch_details: bool = Field(True, description="Whether to fetch additional details for each user. This will slow down the scraping process significantly.")
    max_users_per_subreddit: Optional[int] = Field(
        None,
        ge=1,
        description="Optional per-request override for max users per subreddit.",
    )
