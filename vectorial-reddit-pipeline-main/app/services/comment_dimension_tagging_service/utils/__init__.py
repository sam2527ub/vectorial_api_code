"""Utilities for comment dimension tagging service."""
from .comment_loader import load_comments_from_profiles, is_valid_comment_for_tagging
from .context_builder import (
    build_context_text_for_tagging,
    get_comment_body,
    prepare_comment_for_tagging
)
from .chunk_loader import load_chunk_comments
from .category_selector import get_category_and_dimensions
from .s3_updater import update_comments_in_s3

__all__ = [
    "load_comments_from_profiles",
    "is_valid_comment_for_tagging",
    "build_context_text_for_tagging",
    "get_comment_body",
    "prepare_comment_for_tagging",
    "load_chunk_comments",
    "get_category_and_dimensions",
    "update_comments_in_s3",
]
