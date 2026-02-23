"""Handler for Audience Preview Update: update or create preview for an audience room."""
from typing import Any, Dict, Optional

from fastapi import HTTPException

from app.config import logger, s3_client, s3_bucket
from app.utils.helpers import ensure_db_available
from app import database
from app.services.audience_preview_update_service.config import (
    AudiencePreviewUpdateConfig,
    PREVIEW_PROFILE_LIMIT,
)
from app.services.audience_preview_update_service.utils.preview_builder import (
    generate_preview_for_room,
    get_post_count_from_posts_s3,
    get_comment_count_from_comments_s3,
    update_profile_description_counts,
)


class AudiencePreviewUpdateHandler:
    """Update or create preview cache for an audience room (fast UI rendering)."""

    def __init__(self, config: Optional[AudiencePreviewUpdateConfig] = None):
        self.config = config or AudiencePreviewUpdateConfig()
        self.preview_profile_limit = self.config.preview_profile_limit

    def _validate(self) -> None:
        ensure_db_available("audience")
        if not s3_client or not s3_bucket:
            raise HTTPException(status_code=503, detail="S3 is not configured")

    def update_preview_for_room(
        self,
        room_id: str,
        enterprise_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Update or create preview for a specific audience room.
        Fetches room + profiles from DB, builds preview from S3 data, upserts to previews table.
        Returns the same response shape as POST /api/v1/previews/update-room/{room_id}.
        """
        self._validate()

        database.ensure_preview_table_exists(enterprise_name=enterprise_name)
        room_obj = database.find_audience_room_by_id(
            room_id, include_profiles=True, enterprise_name=enterprise_name
        )
        if not room_obj:
            raise HTTPException(status_code=404, detail=f"Audience room {room_id} not found")

        limit = self.preview_profile_limit
        source = (room_obj.source or "").strip()
        is_reddit = source.lower() == "reddit"

        # Build full profile list with postsS3Url and commentsS3Url for count logic
        all_profiles = [
            {
                "id": p.id,
                "profileName": p.profileName,
                "profileUrl": p.profileUrl,
                "profileDescriptionS3Url": p.profileDescriptionS3Url,
                "postsS3Url": getattr(p, "postsS3Url", None),
                "commentsS3Url": getattr(p, "commentsS3Url", None),
                "source": p.source,
            }
            for p in room_obj.profiles
        ]

        # For each profile: get post count (and comment count for Reddit), update profile description in S3
        for profile in all_profiles:
            post_count = get_post_count_from_posts_s3(
                profile.get("postsS3Url"), source
            )
            comment_count = None
            if is_reddit:
                comment_count = get_comment_count_from_comments_s3(
                    profile.get("commentsS3Url")
                )
            update_profile_description_counts(
                profile.get("profileDescriptionS3Url"),
                post_count,
                comment_count,
                source,
            )
            profile["_post_count"] = post_count

        # Sort by post count descending (top people with most posts first)
        all_profiles.sort(key=lambda p: p.get("_post_count", 0), reverse=True)
        top_profiles = all_profiles[:limit]
        for p in top_profiles:
            p.pop("_post_count", None)

        room = {
            "id": room_obj.id,
            "name": room_obj.name,
            "descriptionS3Url": room_obj.descriptionS3Url,
            "source": room_obj.source,
            "userId": room_obj.userId,
            "profiles": top_profiles,
            "total_profile_count": len(room_obj.profiles),
        }

        logger.info(
            "Generating preview for room %s (top %s by post count)",
            room_id,
            len(top_profiles),
        )
        preview_data = generate_preview_for_room(room, preview_profile_limit=limit)
        database.upsert_preview(
            room_id=preview_data["room_id"],
            name=preview_data["name"],
            user_id=preview_data["user_id"],
            description_summary=preview_data["description_summary"],
            source=preview_data["source"],
            total_profile_count=preview_data["total_profile_count"],
            profiles=preview_data["profiles"],
            enterprise_name=enterprise_name,
        )

        return {
            "status": "success",
            "room_id": room_id,
            "room_name": preview_data["name"],
            "source": preview_data["source"],
            "total_profile_count": preview_data["total_profile_count"],
            "preview_profiles_count": len(preview_data["profiles"]),
            "has_summary": bool(preview_data["description_summary"]),
        }
