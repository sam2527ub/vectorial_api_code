"""Handler for Audience Room Creation - Create Audience Room (POST /api/v1/audience-rooms)."""
import uuid
from typing import Any, Dict

from fastapi import HTTPException

from app.config import logger, s3_client, s3_bucket
from app.utils.helpers import ensure_db_available
from app.utils.s3_utils import ensure_enterprise_audience_folders_exist
from app.services.audience_room_creation_service.repositories import create_room
from app.services.audience_room_creation_service.utils import (
    upload_audience_description,
    upload_audience_indexes_if_present,
    upload_profile_payloads_and_build_records,
)


class AudienceRoomCreationHandler:
    """Creates audience rooms: S3 uploads (description, indexes, profiles) + DB persistence."""

    def _validate(self) -> None:
        ensure_db_available("audience")
        if not s3_client or not s3_bucket:
            logger.error("S3 not configured - missing s3_client or s3_bucket")
            raise HTTPException(
                status_code=503,
                detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME.",
            )

    async def create_audience_room(self, payload: Any) -> Dict[str, Any]:
        """
        Create an audience room: upload description, optional indexes, and profile payloads to S3,
        then persist room and profiles in the audience database.
        Returns the same response shape as POST /api/v1/audience-rooms.
        """
        self._validate()
        logger.info(
            f"create_audience_room: enterpriseName={payload.enterpriseName}, room_name={payload.audience_room_name}, "
            f"profiles_count={len(payload.profiles)}, query={payload.query}, source={payload.source}"
        )

        room_id = str(uuid.uuid4())
        logger.info(f"Generated room_id={room_id}")

        ensure_enterprise_audience_folders_exist(payload.enterpriseName)

        description_url = upload_audience_description(
            room_id=room_id,
            audience_room_name=payload.audience_room_name,
            audience_description=payload.audience_description,
            enterprise_name=payload.enterpriseName,
            source=payload.source,
        )
        logger.info(f"Uploaded description to S3: {description_url}")

        indexes_s3_url = upload_audience_indexes_if_present(
            room_id=room_id,
            query=payload.query,
            search_results=payload.search_results,
            enterprise_name=payload.enterpriseName,
            source=payload.source,
        )
        if indexes_s3_url:
            logger.info(f"Uploaded indexes to S3: {indexes_s3_url}")

        profile_creates = upload_profile_payloads_and_build_records(
            room_id=room_id,
            profiles=payload.profiles,
            enterprise_name=payload.enterpriseName,
            source=payload.source,
        )
        logger.info(f"Uploaded {len(profile_creates)} profile payloads to S3")

        try:
            room = create_room(
                room_id=room_id,
                name=payload.audience_room_name,
                description_s3_url=description_url,
                user_id=payload.userId,
                source=payload.source,
                query=payload.query,
                indexes_s3_url=indexes_s3_url,
                profiles_data=profile_creates,
                enterprise_name=payload.enterpriseName,
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to create audience room: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=f"Failed to create audience room: {str(e)}",
            )

        logger.info(f"Created room id={room.id}, profiles={len(room.profiles)}")
        return {
            "audience_room_id": room.id,
            "audience_room_name": room.name,
            "description_s3_url": room.descriptionS3Url,
            "userId": room.userId,
            "query": room.query,
            "indexes_s3_url": room.indexesS3Url,
            "profiles_created": len(room.profiles),
            "profiles": [
                {
                    "profile_id": p.id,
                    "profile_name": p.profileName,
                    "linkedin_url": p.profileUrl,
                    "profile_description_s3_url": p.profileDescriptionS3Url,
                    "posts_s3_url": p.postsS3Url,
                }
                for p in room.profiles
            ],
        }
