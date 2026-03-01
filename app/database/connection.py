"""
Database utility module - re-exports from pool, models, health, and repositories.
Replaces the previous single-file implementation; see pool, models, health, and repositories.
"""
# Connection / pool
from app.database.pool import (
    get_main_pool,
    get_audience_pool,
    get_enterprise_pool,
    get_main_connection,
    get_audience_connection,
    get_enterprise_audience_connection,
    close_pools,
)
# Health
from app.database.health import (
    check_main_db_connection,
    check_audience_db_connection,
    is_main_db_available,
    is_audience_db_available,
)
# Models
from app.database.models import (
    ScrapeJob,
    ParallelSearchJob,
    CommentScrapeJob,
    AudienceRoom,
    AudienceProfile,
    PostClassifier,
)
# Repositories
from app.database.repositories import scrape_job as _scrape_job
from app.database.repositories import comment_scrape_job as _comment_scrape_job
from app.database.repositories import parallel_search_job as _parallel_search_job
from app.database.repositories import audience_room as _audience_room
from app.database.repositories import audience_profile as _audience_profile
from app.database.repositories import post_classifier as _post_classifier
from app.database.repositories import preview as _preview

# ScrapeJob
create_scrape_job = _scrape_job.create_scrape_job
find_scrape_job_by_id = _scrape_job.find_scrape_job_by_id
update_scrape_job = _scrape_job.update_scrape_job

# CommentScrapeJob
create_comment_scrape_job = _comment_scrape_job.create_comment_scrape_job
find_comment_scrape_job_by_id = _comment_scrape_job.find_comment_scrape_job_by_id
find_latest_comment_scrape_job_by_audience_room_id = _comment_scrape_job.find_latest_comment_scrape_job_by_audience_room_id
update_comment_scrape_job = _comment_scrape_job.update_comment_scrape_job
ensure_comment_scrape_job_table_exists = _comment_scrape_job.ensure_comment_scrape_job_table_exists

# ParallelSearchJob
create_parallel_search_job = _parallel_search_job.create_parallel_search_job
find_parallel_search_job_by_id = _parallel_search_job.find_parallel_search_job_by_id
update_parallel_search_job = _parallel_search_job.update_parallel_search_job

# AudienceRoom
create_audience_room = _audience_room.create_audience_room
find_audience_room_by_id = _audience_room.find_audience_room_by_id
update_audience_room = _audience_room.update_audience_room
delete_audience_room = _audience_room.delete_audience_room
upsert_audience_room = _audience_room.upsert_audience_room
get_user_id_from_enterprise = _audience_room.get_user_id_from_enterprise

# AudienceProfile
find_audience_profiles = _audience_profile.find_audience_profiles
find_audience_profile_by_id = _audience_profile.find_audience_profile_by_id
update_audience_profile = _audience_profile.update_audience_profile
delete_audience_profiles_by_room = _audience_profile.delete_audience_profiles_by_room
upsert_audience_profile = _audience_profile.upsert_audience_profile
delete_audience_profile = _audience_profile.delete_audience_profile

# PostClassifier
find_post_classifier_by_id = _post_classifier.find_post_classifier_by_id
query_first = _post_classifier.query_first

# Preview
find_all_previews = _preview.find_all_previews
find_preview_by_room_id = _preview.find_preview_by_room_id
ensure_preview_table_exists = _preview.ensure_preview_table_exists
upsert_preview = _preview.upsert_preview
delete_preview = _preview.delete_preview
update_preview_name = _preview.update_preview_name
delete_orphaned_previews = _preview.delete_orphaned_previews
find_all_audience_rooms_with_profiles = _preview.find_all_audience_rooms_with_profiles
find_audience_room_with_profiles_for_preview = _preview.find_audience_room_with_profiles_for_preview
