"""Scraping client for profile fetch (Apify LinkedIn scraper)."""
from .interface import ProfileScrapingClientInterface
from .factory import get_scraping_client

__all__ = ["ProfileScrapingClientInterface", "get_scraping_client"]
