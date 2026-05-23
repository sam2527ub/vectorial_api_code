"""Request handler for audience room creation."""
import uuid
import sys
from typing import Dict, Any, List, Optional, Set
from fastapi import HTTPException
from app.config import logger
from app.services.audience_room_creation_service.clients.storage.factory import get_storage_client
from app.utils.helpers import ensure_db_available
from app.services.audience_room_creation_service.repositories import create_audience_room
from app.services.audience_room_creation_service.schemas import AudienceRoomCreationRequest
from app.services.audience_room_creation_service.utils.s3_storage_manager import S3Manager
from app.services.audience_room_creation_service.utils.activity_grouper import ActivityGrouper
from app.services.audience_room_creation_service.utils.profile_creator import ProfileCreator
from app.services.audience_room_creation_service.utils.username_extractor import UsernameExtractor
from app.database.repositories.shared_repositories import (
    find_audience_room_by_id,
    upsert_audience_room_with_profiles,
)


def flush_logs():
    """Force flush stdout/stderr to ensure logs appear immediately."""
    sys.stdout.flush()
    sys.stderr.flush()


def _normalize_reddit_username(raw: Any) -> str:
    """
    Stable key for deduping Reddit users across preview/real. Reddit
    usernames are case-sensitive in display but a single user is reached
    via many URL forms (u/X, /u/X, /user/X). Normalize to bare lowercase
    username so merge is safe.
    """
    if not isinstance(raw, str):
        return ""
    s = raw.strip().lower()
    # Strip protocol / host / known prefixes
    for prefix in ("https://www.reddit.com/", "https://reddit.com/", "www.reddit.com/", "reddit.com/"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    for prefix in ("user/", "u/"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    return s.rstrip("/").strip()


def _user_dedupe_key(user: Dict[str, Any]) -> str:
    """Extract a stable dedupe key from a user dict."""
    extractor = UsernameExtractor.extract_from_user(user)
    candidates = [
        extractor,
        user.get("username"),
        user.get("userId"),
        user.get("userUrl"),
    ]
    for c in candidates:
        key = _normalize_reddit_username(c)
        if key:
            return key
    return ""


def _merge_preview_users_into_real(
    real_users: List[Dict[str, Any]],
    preview_users: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Append preview-only users to the real user list. Dedupe by normalized
    Reddit username. Any preview user that already exists in real is
    discarded (real wins because real has the full set of subreddits to
    filter against). Preserves real ordering; preview extras go at the end.
    """
    seen: Set[str] = set()
    merged: List[Dict[str, Any]] = []
    for u in real_users:
        key = _user_dedupe_key(u)
        if not key:
            # Can't dedupe without a key - keep the user, skip seeding seen.
            merged.append(u)
            continue
        if key in seen:
            continue
        seen.add(key)
        merged.append(u)

    added = 0
    for u in preview_users:
        key = _user_dedupe_key(u)
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(u)
        added += 1

    logger.info(
        "Merged preview users into real: real_before=%s preview_in=%s added=%s total=%s",
        len(real_users), len(preview_users), added, len(merged),
    )
    return merged


class AudienceRoomCreationHandler:
    """Handles audience room creation requests."""
    
    def __init__(self):
        """Initialize request handler."""
        self.s3_manager = S3Manager()
        self.activity_grouper = ActivityGrouper()
        self.profile_creator = ProfileCreator()
    
    async def handle_request(
        self,
        payload: AudienceRoomCreationRequest
    ) -> Dict[str, Any]:
        """Handle audience room creation request."""
        logger.info("=" * 80)
        logger.info("REQUEST RECEIVED: Audience Room Creation")
        logger.info(f"Request Details:")
        logger.info(f"  - Audience Room Name: {payload.audience_room_name}")
        logger.info(f"  - Enterprise Name: {payload.enterpriseName}")
        logger.info(f"  - User ID: {payload.userId}")
        logger.info(f"  - Source: {payload.source}")
        logger.info(f"  - Query: {payload.query}")
        logger.info(f"  - Users Count: {len(payload.users)}")
        logger.info(f"  - Search Results Count: {len(payload.search_results)}")
        logger.info(f"  - Activities Count: {len(payload.activities) if payload.activities else 0}")
        logger.info(f"  - Activities S3 URL: {payload.activities_s3_url}")
        logger.info(f"  - Subreddit Similarity Data: {'Provided' if payload.subreddit_similarity_data else 'Not provided'}")
        logger.info(f"  - audience_room_id (pre-allocated): {getattr(payload, 'audience_room_id', None)}")
        logger.info(f"  - is_preview: {getattr(payload, 'is_preview', False)}")
        logger.info(f"  - is_preview_then_real: {getattr(payload, 'is_preview_then_real', False)}")
        flush_logs()

        ensure_db_available("audience")
        storage_client = get_storage_client()
        if not storage_client.is_configured() or not storage_client.get_bucket_name():
            raise HTTPException(
                status_code=503,
                detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME."
            )

        # Preview-then-Real workflow: a stable id is pre-allocated and sent by
        # the Vercel Workflow so preview and real share one AudienceRoom row.
        # Legacy flow: no id supplied -> generate a new one like before.
        provided_room_id = getattr(payload, 'audience_room_id', None)
        is_preview_call = bool(getattr(payload, 'is_preview', False))
        is_preview_then_real = bool(getattr(payload, 'is_preview_then_real', False))

        existing_room = (
            find_audience_room_by_id(provided_room_id, include_profiles=True, enterprise_name=payload.enterpriseName)
            if provided_room_id
            else None
        )
        replace_mode = bool(existing_room)

        # Safety guard for the Preview-then-Real workflow: if the preview
        # pipeline somehow arrives after the real pipeline finished and set
        # status=READY, DO NOT destructively replace 100 real profiles with
        # 15 preview ones. Return the existing room untouched.
        if is_preview_call and existing_room is not None and existing_room.status == 'READY':
            logger.warning(
                "Skipping preview upsert: room %s is already status=READY "
                "(real pipeline won the race). Returning existing room as-is.",
                provided_room_id,
            )
            return {
                "audience_room_id": existing_room.id,
                "audience_room_name": existing_room.name,
                "description_s3_url": existing_room.descriptionS3Url,
                "indexes_s3_url": existing_room.indexesS3Url,
                "userId": existing_room.userId,
                "query": existing_room.query,
                "source": existing_room.source,
                "profiles_created": len(existing_room.profiles or []),
                "profiles": [
                    {
                        "profile_id": p.id,
                        "profile_name": p.profileName,
                        "profile_url": p.profileUrl,
                        "profile_description_s3_url": p.profileDescriptionS3Url,
                        "posts_s3_url": p.postsS3Url,
                        "comments_s3_url": p.commentsS3Url,
                    }
                    for p in (existing_room.profiles or [])
                ],
                "skipped_reason": "room_already_ready",
            }

        room_id = provided_room_id or str(uuid.uuid4())
        logger.info(
            "Using room_id=%s (generated=%s, replace_mode=%s, is_preview=%s, is_preview_then_real=%s)",
            room_id, provided_room_id is None, replace_mode, is_preview_call, is_preview_then_real,
        )
        flush_logs()
        
        # Ensure enterprise folder exists
        self.s3_manager.ensure_enterprise_folders(payload.enterpriseName)
        
        try:
            # Fetch activities from S3 or payload
            logger.info("Fetching activities...")
            flush_logs()
            activities = self.s3_manager.fetch_activities(
                payload.activities,
                payload.activities_s3_url
            )
            logger.info(f"Total activities to process: {len(activities)}")
            flush_logs()
            
            # Upload room description and indexes
            logger.info("Uploading room description and indexes...")
            flush_logs()
            description_url = self.s3_manager.upload_room_description(
                room_id,
                payload.audience_room_name,
                payload.audience_room_description,
                payload.enterpriseName,
                payload.source
            )
            logger.info("Description uploaded")
            flush_logs()
            
            indexes_url = self.s3_manager.upload_indexes(
                room_id,
                payload.query,
                payload.search_results,
                payload.enterpriseName,
                payload.source
            )
            logger.info("Indexes uploaded")
            flush_logs()
            
            # Upload subreddit similarity data if provided
            if payload.subreddit_similarity_data:
                try:
                    logger.info("Uploading subreddit similarity data...")
                    flush_logs()
                    self.s3_manager.upload_subreddit_similarity(
                        room_id,
                        payload.subreddit_similarity_data,
                        payload.enterpriseName,
                        payload.source
                    )
                    logger.info("Subreddit similarity data uploaded")
                    flush_logs()
                except Exception as e:
                    logger.warning(f"Failed to upload subreddit similarity data: {e}")
                    flush_logs()
                    # Don't fail room creation if this fails
            
            # Group activities by username
            logger.info("Grouping activities by username...")
            flush_logs()
            user_activities_map = self.activity_grouper.group_by_username(activities)
            logger.info(f"Grouped activities for {len(user_activities_map)} unique users")
            flush_logs()
            
            # Create profiles and upload to S3
            logger.info(f"Processing {len(payload.users)} users...")
            flush_logs()
            profile_creates = self.profile_creator.create_profiles(
                payload.users,
                user_activities_map,
                room_id,
                payload.enterpriseName,
                payload.source
            )
            logger.info(f"Created {len(profile_creates)} profiles")
            flush_logs()

            # Preview-then-Real: on the real pipeline's upsert, preserve the
            # preview-only profiles (those whose username doesn't collide with
            # any real user). Their existing S3 artifacts + AudienceProfile.id
            # are retained so downstream services (chat, RAG, history) that
            # stored references during the preview stay valid.
            if (
                replace_mode
                and is_preview_then_real
                and not is_preview_call
                and existing_room is not None
                and (existing_room.profiles or [])
            ):
                real_keys = {
                    _normalize_reddit_username(pc.get('profileName'))
                    for pc in profile_creates
                    if pc.get('profileName')
                }
                real_keys.discard("")
                preserved: List[Dict[str, Any]] = []
                for old in existing_room.profiles:
                    key = _normalize_reddit_username(old.profileName)
                    if not key or key in real_keys:
                        continue
                    preserved.append({
                        # Reuse the existing profile id so any outside reference
                        # to this profile (e.g. chat history) survives the upsert.
                        'id': old.id,
                        'profileName': old.profileName,
                        'profileUrl': old.profileUrl,
                        'profileDescriptionS3Url': old.profileDescriptionS3Url,
                        'postsS3Url': old.postsS3Url,
                        'commentsS3Url': old.commentsS3Url,
                        'source': old.source,
                    })
                if preserved:
                    logger.info(
                        "Preserving %s preview-only profiles in real upsert (real=%s, preview-only=%s)",
                        len(preserved), len(profile_creates), len(preserved),
                    )
                    profile_creates = profile_creates + preserved
                else:
                    logger.info(
                        "No preview-only profiles to preserve (all preview users overlap real set)"
                    )

            # Create or upsert room in database
            if replace_mode:
                logger.info(
                    "Upserting room in database (replace-in-place mode, preview-then-real=%s)",
                    is_preview_then_real,
                )
                flush_logs()
                room = upsert_audience_room_with_profiles(
                    room_id=room_id,
                    name=payload.audience_room_name,
                    description_s3_url=description_url,
                    user_id=payload.userId,
                    source=payload.source,
                    query=payload.query,
                    indexes_s3_url=indexes_url,
                    category=payload.category,
                    profiles_data=profile_creates,
                    enterprise_name=payload.enterpriseName,
                    replace_profiles=True,
                )
                logger.info(f"Room upserted with {len(room.profiles)} profiles")
                flush_logs()
            else:
                logger.info("Creating room in database...")
                flush_logs()
                room = create_audience_room(
                    room_id=room_id,
                    name=payload.audience_room_name,
                    description_s3_url=description_url,
                    user_id=payload.userId,
                    source=payload.source,
                    query=payload.query,
                    indexes_s3_url=indexes_url,
                    profiles_data=profile_creates,
                    enterprise_name=payload.enterpriseName,
                    category=payload.category,
                )
                logger.info(f"Room created with {len(room.profiles)} profiles")
                flush_logs()
            
            # Build response
            response = {
                "audience_room_id": room.id,
                "audience_room_name": room.name,
                "description_s3_url": room.descriptionS3Url,
                "indexes_s3_url": room.indexesS3Url,
                "userId": room.userId,
                "query": room.query,
                "source": room.source,
                "profiles_created": len(room.profiles),
                "profiles": [
                    {
                        "profile_id": p.id,
                        "profile_name": p.profileName,
                        "profile_url": p.profileUrl,
                        "profile_description_s3_url": p.profileDescriptionS3Url,
                        "posts_s3_url": p.postsS3Url,
                        "comments_s3_url": p.commentsS3Url,
                    }
                    for p in room.profiles
                ],
            }
            
            logger.info("RESPONSE SENT: Audience Room Creation")
            logger.info(f"Response Details:")
            logger.info(f"  - Audience Room ID: {response.get('audience_room_id')}")
            logger.info(f"  - Audience Room Name: {response.get('audience_room_name')}")
            logger.info(f"  - Profiles Created: {response.get('profiles_created')}")
            logger.info("=" * 80)
            flush_logs()
            
            return response
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error creating audience room: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=f"Error creating Reddit audience room: {str(e)}"
            )
