"""Resolves audience room and chunk counts for comment context summary job processing."""

from typing import Any, Dict, Optional, Tuple

from app import database
from app.config import logger

from app.services.comment_context_summary_service.repositories.comment_context_summary_job_repository import (
    get_comment_context_summary_job,
    update_comment_context_summary_job,
)


def resolve_room_and_chunk_info_for_job(
    job_id: str,
    audience_room_id: str,
    enterprise_name: Optional[str],
    start_chunk: int,
    chunk_size_profiles: int,
    job: Dict[str, Any],
) -> Tuple[Optional[Any], Optional[list], int, int]:
    """
    Load room and set job total_profiles/total_chunks on first chunk.
    Returns (room, all_profiles, total_profiles, total_chunks) or (None, None, 0, 0) on failure.
    """
    if start_chunk == 0:
        room = database.find_audience_room_by_id(
            audience_room_id,
            include_profiles=True,
            enterprise_name=enterprise_name,
        )
        if not room:
            update_comment_context_summary_job(
                job_id,
                enterprise_name,
                status="FAILED",
                error=f"Audience room {audience_room_id} not found",
            )
            return None, None, 0, 0

        all_profiles = room.profiles or []
        total_profiles = len(all_profiles)
        total_chunks = (total_profiles + chunk_size_profiles - 1) // chunk_size_profiles if total_profiles else 0

        if total_profiles == 0:
            update_comment_context_summary_job(
                job_id,
                enterprise_name,
                status="COMPLETED",
                total_profiles=0,
                total_chunks=0,
            )
            return room, [], 0, 0

        update_comment_context_summary_job(
            job_id,
            enterprise_name,
            total_profiles=total_profiles,
            total_chunks=total_chunks,
            status="PROCESSING",
        )
        logger.info(
            "[CommentContextSummaryJob %s] Processing %s profiles in %s chunks",
            job_id,
            total_profiles,
            total_chunks,
        )
        return room, all_profiles, total_profiles, total_chunks
    else:
        job = get_comment_context_summary_job(job_id, enterprise_name)
        total_profiles = job["total_profiles"]
        total_chunks = job["total_chunks"]
        room = database.find_audience_room_by_id(
            audience_room_id,
            include_profiles=True,
            enterprise_name=enterprise_name,
        )
        if not room:
            update_comment_context_summary_job(
                job_id,
                enterprise_name,
                status="FAILED",
                error=f"Audience room {audience_room_id} not found",
            )
            return None, None, 0, 0
        all_profiles = room.profiles or []
        return room, all_profiles, total_profiles, total_chunks
