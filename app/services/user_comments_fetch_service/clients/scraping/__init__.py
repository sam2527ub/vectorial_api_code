"""Scraping clients for LinkedIn profile comments."""
from .interface import CommentScraperClientInterface
from .factory import get_comment_scraper_client

__all__ = ["CommentScraperClientInterface", "get_comment_scraper_client"]
