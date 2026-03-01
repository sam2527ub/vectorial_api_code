#!/usr/bin/env python3
"""
Verify database refactor: all imports and expected API exist.
Run from repo root: python scripts/verify_database.py
"""
import sys

def main():
    errors = []

    # 1) All symbols from app.database (what __init__.py exports)
    try:
        from app import database
    except Exception as e:
        errors.append(f"from app import database: {e}")
        return errors

    expected_on_database = [
        "get_main_pool", "get_audience_pool", "get_enterprise_pool",
        "get_main_connection", "get_audience_connection", "get_enterprise_audience_connection",
        "close_pools",
        "check_main_db_connection", "check_audience_db_connection",
        "is_main_db_available", "is_audience_db_available",
        "ScrapeJob", "ParallelSearchJob", "CommentScrapeJob",
        "AudienceRoom", "AudienceProfile", "PostClassifier",
        "create_scrape_job", "find_scrape_job_by_id", "update_scrape_job",
        "create_comment_scrape_job", "find_comment_scrape_job_by_id",
        "find_latest_comment_scrape_job_by_audience_room_id", "update_comment_scrape_job",
        "ensure_comment_scrape_job_table_exists",
        "create_parallel_search_job", "find_parallel_search_job_by_id", "update_parallel_search_job",
        "create_audience_room", "find_audience_room_by_id", "update_audience_room",
        "delete_audience_room", "upsert_audience_room", "get_user_id_from_enterprise",
        "find_audience_profiles", "find_audience_profile_by_id", "update_audience_profile",
        "delete_audience_profiles_by_room", "upsert_audience_profile",
        "find_post_classifier_by_id",
        "find_all_previews", "find_preview_by_room_id", "ensure_preview_table_exists",
        "upsert_preview", "update_preview_name", "delete_preview",
        "delete_orphaned_previews", "find_all_audience_rooms_with_profiles",
        "find_audience_room_with_profiles_for_preview",
        "query_first",
    ]
    for name in expected_on_database:
        if not hasattr(database, name):
            errors.append(f"database.{name} missing")

    # 2) Direct connection imports (used by repos and services)
    try:
        from app.database.connection import (
            get_enterprise_audience_connection,
            get_main_connection,
            get_audience_connection,
        )
        # Callables
        assert callable(get_enterprise_audience_connection)
        assert callable(get_main_connection)
        assert callable(get_audience_connection)
    except Exception as e:
        errors.append(f"app.database.connection imports: {e}")

    # 3) Models have expected attributes (from row dict)
    try:
        from app.database.models import ScrapeJob, AudienceRoom, AudienceProfile, PostClassifier
        # ScrapeJob from a minimal row
        row = {"id": "x", "status": "PENDING"}
        j = ScrapeJob(row)
        assert j.id == "x" and j.status == "PENDING"
        # AudienceRoom
        r = AudienceRoom({"id": "r1", "name": "n"})
        assert r.id == "r1" and r.name == "n" and hasattr(r, "profiles")
        # AudienceProfile
        p = AudienceProfile({"id": "p1", "audienceRoomId": "r1"})
        assert p.id == "p1" and p.audienceRoomId == "r1"
        # PostClassifier labels/examples
        pc = PostClassifier({"id": "c1", "labels": '["a"]', "examples": None})
        assert pc.id == "c1" and isinstance(pc.labels, list)
    except Exception as e:
        errors.append(f"Model instantiation: {e}")

    # 4) audience_room_repository.create_room uses database.create_audience_room with same signature
    try:
        from app.services.audience_room_creation_service.repositories.audience_room_repository import create_room
        import inspect
        sig = inspect.signature(create_room)
        params = list(sig.parameters)
        assert "room_id" in params and "profiles_data" in params and "enterprise_name" in params
        # create_audience_room signature
        sig_db = inspect.signature(database.create_audience_room)
        assert "profiles_data" in sig_db.parameters and "enterprise_name" in sig_db.parameters
    except Exception as e:
        errors.append(f"create_room/create_audience_room signature: {e}")

    return errors


if __name__ == "__main__":
    errs = main()
    if errs:
        for e in errs:
            print("ERROR:", e, file=sys.stderr)
        sys.exit(1)
    print("OK: All database API checks passed.")
    sys.exit(0)
