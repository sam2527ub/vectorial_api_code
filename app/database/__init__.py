"""
Database module - provides database connection and query functions.
"""

from app.database.connection import (
    # Connection management
    get_main_pool,
    get_audience_pool,
    get_enterprise_pool,
    get_main_connection,
    get_audience_connection,
    get_enterprise_audience_connection,
    close_pools,
    
    # Database health checks
    check_main_db_connection,
    check_audience_db_connection,
    is_main_db_available,
    is_audience_db_available,
    
    # Data models
    ScrapeJob,
    ParallelSearchJob,
    CommentScrapeJob,
    AudienceRoom,
    AudienceProfile,
    PostClassifier,
    
    # ScrapeJob operations (audience database)
    create_scrape_job,
    find_scrape_job_by_id,
    update_scrape_job,

    create_comment_scrape_job,
    find_comment_scrape_job_by_id,
    find_latest_comment_scrape_job_by_audience_room_id,
    update_comment_scrape_job,
    ensure_comment_scrape_job_table_exists,

    # ParallelSearchJob operations (audience database)
    create_parallel_search_job,
    find_parallel_search_job_by_id,
    update_parallel_search_job,
    
    # AudienceRoom operations (audience database)
    create_audience_room,
    find_audience_room_by_id,
    update_audience_room,
    delete_audience_room,
    upsert_audience_room,
    get_user_id_from_enterprise,
    
    # AudienceProfile operations (audience database)
    find_audience_profiles,
    find_audience_profile_by_id,
    update_audience_profile,
    delete_audience_profiles_by_room,
    upsert_audience_profile,
    
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
    "get_enterprise_pool",
    "get_main_connection",
    "get_audience_connection",
    "get_enterprise_audience_connection",
    "close_pools",
    "check_main_db_connection",
    "check_audience_db_connection",
    "is_main_db_available",
    "is_audience_db_available",
    "ScrapeJob",
    "ParallelSearchJob",
    "CommentScrapeJob",
    "AudienceRoom",
    "AudienceProfile",
    "PostClassifier",
    "create_scrape_job",
    "find_scrape_job_by_id",
    "update_scrape_job",
    "create_comment_scrape_job",
    "find_comment_scrape_job_by_id",
    "find_latest_comment_scrape_job_by_audience_room_id",
    "update_comment_scrape_job",
    "ensure_comment_scrape_job_table_exists",
    "create_parallel_search_job",
    "find_parallel_search_job_by_id",
    "update_parallel_search_job",
    "create_audience_room",
    "find_audience_room_by_id",
    "update_audience_room",
    "delete_audience_room",
    "upsert_audience_room",
    "get_user_id_from_enterprise",
    "find_audience_profiles",
    "find_audience_profile_by_id",
    "update_audience_profile",
    "delete_audience_profiles_by_room",
    "upsert_audience_profile",
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
