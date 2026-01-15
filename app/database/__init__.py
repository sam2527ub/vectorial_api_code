"""
Database module - provides database connection and query functions.
"""

from app.database.connection import (
    # Connection management
    get_main_pool,
    get_audience_pool,
    get_main_connection,
    get_audience_connection,
    close_pools,
    
    # Database health checks
    check_main_db_connection,
    check_audience_db_connection,
    is_main_db_available,
    is_audience_db_available,
    
    # Data models
    ScrapeJob,
    AudienceRoom,
    AudienceProfile,
    PostClassifier,
    
    # ScrapeJob operations (main database)
    create_scrape_job,
    find_scrape_job_by_id,
    update_scrape_job,
    
    # AudienceRoom operations (audience database)
    create_audience_room,
    find_audience_room_by_id,
    update_audience_room,
    delete_audience_room,
    
    # AudienceProfile operations (audience database)
    find_audience_profiles,
    find_audience_profile_by_id,
    update_audience_profile,
    delete_audience_profiles_by_room,
    
    # PostClassifier operations (audience database)
    find_post_classifier_by_id,
    
    # Preview operations (audience database)
    find_all_previews,
    find_preview_by_room_id,
    ensure_preview_table_exists,
    upsert_preview,
    update_preview_name,
    delete_preview,
    delete_orphaned_previews,
    find_all_audience_rooms_with_profiles,
    find_audience_room_with_profiles_for_preview,
    
    # Raw query helper
    query_first,
)

__all__ = [
    "get_main_pool",
    "get_audience_pool",
    "get_main_connection",
    "get_audience_connection",
    "close_pools",
    "check_main_db_connection",
    "check_audience_db_connection",
    "is_main_db_available",
    "is_audience_db_available",
    "ScrapeJob",
    "AudienceRoom",
    "AudienceProfile",
    "PostClassifier",
    "create_scrape_job",
    "find_scrape_job_by_id",
    "update_scrape_job",
    "create_audience_room",
    "find_audience_room_by_id",
    "update_audience_room",
    "delete_audience_room",
    "find_audience_profiles",
    "find_audience_profile_by_id",
    "update_audience_profile",
    "delete_audience_profiles_by_room",
    "find_post_classifier_by_id",
    "find_all_previews",
    "find_preview_by_room_id",
    "ensure_preview_table_exists",
    "upsert_preview",
    "update_preview_name",
    "delete_preview",
    "delete_orphaned_previews",
    "find_all_audience_rooms_with_profiles",
    "find_audience_room_with_profiles_for_preview",
    "query_first",
]

