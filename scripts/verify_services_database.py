#!/usr/bin/env python3
"""
Import every service (and app) module that uses the database. Fail if any import or attribute access breaks.
Run from repo root: PYTHONPATH=. python scripts/verify_services_database.py
"""
import sys
import os

# Ensure we can import app
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def main():
    errors = []

    # Modules that use "from app import database" or "from app.database.connection import ..."
    modules_to_import = [
        "app.main",
        "app.config",
        "app.__init__",
        "app.utils.helpers",
        "app.utils.s3_utils",
        "app.services.audience_group_summarization_service.audience_group_summarization_handler",
        "app.services.audience_room_creation_service.repositories.audience_room_repository",
        "app.services.comment_context_summary_service.comment_context_summary_async_handler",
        "app.services.user_observation_fetch_service.profile_processor",
        "app.services.user_profile_summarization_service.repositories.summaries_job_repository",
        "app.services.user_comments_fetch_service.comment_processor",
        "app.services.audience_preview_update_service.audience_preview_update_handler",
        "app.services.comment_context_summary_service.comment_context_summary_chunk_processor_room_resolver",
        "app.services.user_post_classifier_service.job_processor",
        "app.services.comment_context_summary_service.repositories.comment_context_summary_job_repository_create",
        "app.services.user_comments_fetch_service.user_comments_fetch_handler",
        "app.services.comment_context_summary_service.repositories.comment_context_summary_job_table_setup",
        "app.services.user_profile_summarization_service.user_profile_summarization_handler",
        "app.services.user_profile_summarization_service.profile_summary_processor",
        "app.services.user_post_classifier_service.user_post_classifier_handler",
        "app.services.user_post_classifier_service.repositories.classifier_job_repository",
        "app.services.comment_context_summary_service.repositories.comment_context_summary_job_repository_read_update",
        "app.services.user_observation_fetch_service.repositories.scrape_job_repository",
    ]

    for mod_name in modules_to_import:
        try:
            __import__(mod_name)
        except Exception as e:
            errors.append(f"{mod_name}: {e}")

    # Verify key database attributes used by services
    try:
        from app import database
        _ = (
            database.create_audience_room,
            database.find_audience_room_by_id,
            database.create_scrape_job,
            database.find_scrape_job_by_id,
            database.update_scrape_job,
            database.find_comment_scrape_job_by_id,
            database.update_comment_scrape_job,
            database.create_comment_scrape_job,
            database.get_enterprise_audience_connection,
            database.find_post_classifier_by_id,
            database.query_first,
            database.upsert_preview,
            database.find_preview_by_room_id,
            database.find_all_audience_rooms_with_profiles,
            database.find_audience_room_with_profiles_for_preview,
            database.ensure_preview_table_exists,
            database.update_preview_name,
            database.delete_preview,
            database.find_audience_profiles,
            database.find_audience_profile_by_id,
        )
    except Exception as e:
        errors.append(f"database attribute check: {e}")

    return errors


if __name__ == "__main__":
    errs = main()
    if errs:
        for e in errs:
            print("ERROR:", e, file=sys.stderr)
        sys.exit(1)
    print("OK: All service imports and database usage verified.")
    sys.exit(0)
