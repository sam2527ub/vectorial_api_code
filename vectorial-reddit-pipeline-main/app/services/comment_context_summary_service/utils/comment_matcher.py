"""Utilities for matching comments between comments.json and scraper output."""
from typing import Dict, Any, Optional, List


def flatten_comments_recursive(comments: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Flatten nested comments structure into a map: id -> comment."""
    comment_map = {}
    
    def traverse(comment_list: List[Dict[str, Any]]):
        for comment in comment_list:
            if not isinstance(comment, dict):
                continue
            
            comment_id = comment.get("id")
            if comment_id:
                comment_map[comment_id] = comment
            
            replies = comment.get("replies", [])
            if replies and isinstance(replies, list):
                traverse(replies)
    
    traverse(comments)
    return comment_map


def find_post_in_scraper_output(
    post_id: str,
    scraper_items: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Find post in scraper output by postId."""
    short_post_id = post_id.replace("t3_", "") if post_id.startswith("t3_") else post_id
    
    for item in scraper_items:
        if not isinstance(item, dict):
            continue
        
        item_type = item.get("type")
        if item_type == "post":
            item_id = item.get("id", "")
            if item_id == post_id or item_id == short_post_id or item_id.replace("t3_", "") == short_post_id:
                return item
    
    return None


def find_comment_in_scraper_output(
    comment_id: str,
    scraper_items: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Find comment in scraper output by comment id."""
    comment_map = {}
    
    for item in scraper_items:
        if not isinstance(item, dict):
            continue
        
        if item.get("type") == "post":
            comments = item.get("comments", [])
            if comments:
                comment_map.update(flatten_comments_recursive(comments))
        elif item.get("type") == "comment":
            item_id = item.get("id")
            if item_id:
                comment_map[item_id] = item
            replies = item.get("replies", [])
            if replies:
                comment_map.update(flatten_comments_recursive(replies))
    
    return comment_map.get(comment_id)
