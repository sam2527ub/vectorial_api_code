"""Preview-then-Real workflow additions for AudienceRoom.

Three pieces live here, all feature-flagged behind the Preview-then-Real
Vercel Workflow entrypoint and completely invisible to the legacy flow:

1. ensure_audience_room_preview_columns_exist - idempotent "soft migration"
   that runs ALTER TABLE ... ADD COLUMN IF NOT EXISTS for the four nullable
   tracking columns (status / isPreview / previewReadyAt / fullReadyAt) plus
   a status index. Non-fatal on permission errors so the service can still
   boot if ALTER TABLE isn't granted; the guard just reduces to a no-op.

2. upsert_audience_room_with_profiles - the idempotent/replace variant of
   create_audience_room. Used when the real pipeline runs against a room
   the preview pipeline already populated: room metadata is upserted, old
   AudienceProfile rows are deleted, and the incoming ones are inserted,
   all inside one transaction so no reader sees a half-rebuilt room.

3. set_audience_room_status - updates ONLY the four tracking columns. The
   canonical data columns (descriptionS3Url, profiles, etc.) are untouched.
   Used by the markUpgrading / markPreviewReady / finalizeAudienceRoom
   workflow step helpers.
"""
import logging
import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any, Set

from psycopg2 import errors as psycopg2_errors
from psycopg2.extras import RealDictCursor

from app.database.database_connection_pool_manager import get_enterprise_audience_connection
from app.database.models.audience_room import AudienceRoom
from app.database.models.audience_profile import AudienceProfile

logger = logging.getLogger(__name__)

# Per-process cache so we only run the ALTER TABLE dance once per
# (enterprise, process) combination. Each entry is the enterprise key
# ("__default__" for None).
_PREVIEW_COLUMNS_ENSURED: Set[str] = set()


def _enterprise_cache_key(enterprise_name: Optional[str]) -> str:
    return enterprise_name or "__default__"


def ensure_audience_room_preview_columns_exist(enterprise_name: Optional[str] = None) -> None:
    """
    Lazy, idempotent, additive "soft migration" that guarantees the four
    Preview-then-Real tracking columns exist on the AudienceRoom table.

    - Safe to call many times; subsequent calls in the same process are
      no-ops thanks to _PREVIEW_COLUMNS_ENSURED.
    - Uses ADD COLUMN IF NOT EXISTS, so it never errors if the column is
      already there.
    - Swallows permission errors so a DB role without DDL still boots - the
      code paths that require the columns will simply return None values.
    """
    cache_key = _enterprise_cache_key(enterprise_name)
    if cache_key in _PREVIEW_COLUMNS_ENSURED:
        return

    ddl_statements = [
        'ALTER TABLE "AudienceRoom" ADD COLUMN IF NOT EXISTS "status" TEXT',
        'ALTER TABLE "AudienceRoom" ADD COLUMN IF NOT EXISTS "isPreview" BOOLEAN',
        'ALTER TABLE "AudienceRoom" ADD COLUMN IF NOT EXISTS "previewReadyAt" TIMESTAMP',
        'ALTER TABLE "AudienceRoom" ADD COLUMN IF NOT EXISTS "fullReadyAt" TIMESTAMP',
        'CREATE INDEX IF NOT EXISTS "AudienceRoom_status_idx" ON "AudienceRoom"("status")',
    ]
    try:
        with get_enterprise_audience_connection(enterprise_name) as conn:
            with conn.cursor() as cur:
                for ddl in ddl_statements:
                    try:
                        cur.execute(ddl)
                    except psycopg2_errors.InsufficientPrivilege as e:
                        logger.warning(
                            "Skipping preview-column DDL (no DDL privilege): %s | %s",
                            ddl, e,
                        )
                        conn.rollback()
                        # we still mark as ensured to avoid retry storms
                        break
                    except Exception as e:
                        logger.warning(
                            "Soft-migration statement failed (non-fatal): %s | %s",
                            ddl, e,
                        )
                        conn.rollback()
                else:
                    conn.commit()
        _PREVIEW_COLUMNS_ENSURED.add(cache_key)
        logger.info(
            "AudienceRoom preview columns ensured (enterprise=%s)", cache_key
        )
    except Exception as e:
        # Never fail startup / request on migration issues
        logger.warning(
            "Could not ensure AudienceRoom preview columns (non-fatal): %s", e
        )
        _PREVIEW_COLUMNS_ENSURED.add(cache_key)


def upsert_audience_room_with_profiles(
    room_id: str,
    name: str,
    description_s3_url: str,
    user_id: Optional[str] = None,
    source: Optional[str] = None,
    query: Optional[str] = None,
    indexes_s3_url: Optional[str] = None,
    category: Optional[str] = None,
    profiles_data: Optional[List[Dict[str, Any]]] = None,
    enterprise_name: Optional[str] = None,
    replace_profiles: bool = True,
) -> AudienceRoom:
    """
    Idempotent create/replace for an AudienceRoom used by the Preview-then-Real
    Reddit workflow. Behaves as `create_audience_room` when the room does not
    exist, and as a replace-in-place when it does:

      * room metadata columns are upserted
      * if replace_profiles=True, existing AudienceProfile rows for this room
        are deleted and the supplied profiles_data re-inserted

    Everything runs inside a single transaction so readers never see a half-
    rebuilt room. Status columns (status / isPreview / previewReadyAt /
    fullReadyAt) are intentionally NOT touched here - they're owned by the
    workflow orchestrator and mutated through set_audience_room_status below.
    """
    ensure_audience_room_preview_columns_exist(enterprise_name)
    now = datetime.utcnow()

    upsert_sql = """
        INSERT INTO "AudienceRoom"
          (id, name, "descriptionS3Url", "userId", "source", "query",
           "indexesS3Url", "createdAt", "updatedAt", category)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET
            name = EXCLUDED.name,
            "descriptionS3Url" = EXCLUDED."descriptionS3Url",
            "userId" = COALESCE(EXCLUDED."userId", "AudienceRoom"."userId"),
            "source" = COALESCE(EXCLUDED."source", "AudienceRoom"."source"),
            "query" = COALESCE(EXCLUDED."query", "AudienceRoom"."query"),
            "indexesS3Url" = COALESCE(EXCLUDED."indexesS3Url", "AudienceRoom"."indexesS3Url"),
            category = COALESCE(EXCLUDED.category, "AudienceRoom".category),
            "updatedAt" = EXCLUDED."updatedAt"
        RETURNING *
    """
    params = (
        room_id, name, description_s3_url, user_id, source, query,
        indexes_s3_url, now, now, category,
    )

    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(upsert_sql, params)
            room_row = cur.fetchone()
            if not room_row:
                raise RuntimeError(f"AudienceRoom upsert failed (id={room_id})")
            room = AudienceRoom(room_row)

            if replace_profiles:
                cur.execute(
                    'DELETE FROM "AudienceProfile" WHERE "audienceRoomId" = %s',
                    (room_id,),
                )

            if profiles_data:
                for profile_data in profiles_data:
                    profile_id = profile_data.get('id', str(uuid.uuid4()))
                    cur.execute(
                        """
                        INSERT INTO "AudienceProfile"
                            (id, "audienceRoomId", "profileName", "profileUrl",
                             "profileDescriptionS3Url", "postsS3Url", "commentsS3Url",
                             "source", "createdAt", "updatedAt")
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING *
                        """,
                        (
                            profile_id,
                            room_id,
                            profile_data.get('profileName'),
                            profile_data.get('profileUrl') or profile_data.get('linkedinUrl'),
                            profile_data.get('profileDescriptionS3Url'),
                            profile_data.get('postsS3Url'),
                            profile_data.get('commentsS3Url'),
                            profile_data.get('source'),
                            now,
                            now,
                        ),
                    )
                    profile_row = cur.fetchone()
                    if profile_row:
                        room.profiles.append(AudienceProfile(profile_row))
            return room


def set_audience_room_status(
    room_id: str,
    status: Optional[str] = None,
    is_preview: Optional[bool] = None,
    preview_ready_at: Optional[datetime] = None,
    full_ready_at: Optional[datetime] = None,
    enterprise_name: Optional[str] = None,
) -> Optional[AudienceRoom]:
    """
    Mutate only the preview-workflow tracking columns on AudienceRoom. All
    arguments are optional; only supplied ones are written. Columns are
    auto-provisioned on first call via ensure_audience_room_preview_columns_exist().
    """
    ensure_audience_room_preview_columns_exist(enterprise_name)

    set_clauses: List[str] = []
    values: List[Any] = []
    if status is not None:
        set_clauses.append('"status" = %s')
        values.append(status)
    if is_preview is not None:
        set_clauses.append('"isPreview" = %s')
        values.append(is_preview)
    if preview_ready_at is not None:
        set_clauses.append('"previewReadyAt" = %s')
        values.append(preview_ready_at)
    if full_ready_at is not None:
        set_clauses.append('"fullReadyAt" = %s')
        values.append(full_ready_at)

    if not set_clauses:
        # Nothing to do - return the current row so callers can still use it
        with get_enterprise_audience_connection(enterprise_name) as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute('SELECT * FROM "AudienceRoom" WHERE id = %s', (room_id,))
                row = cur.fetchone()
                return AudienceRoom(row) if row else None

    set_clauses.append('"updatedAt" = %s')
    values.append(datetime.utcnow())
    values.append(room_id)

    with get_enterprise_audience_connection(enterprise_name) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            try:
                cur.execute(
                    f'UPDATE "AudienceRoom" SET {", ".join(set_clauses)} WHERE id = %s RETURNING *',
                    values,
                )
                row = cur.fetchone()
                return AudienceRoom(row) if row else None
            except psycopg2_errors.UndefinedColumn as e:
                # Columns aren't provisioned and we can't create them (no DDL
                # privileges). Non-fatal: pretend the status update was a no-op.
                logger.warning(
                    "set_audience_room_status skipped - columns missing and "
                    "cannot be created: %s", e,
                )
                return None
