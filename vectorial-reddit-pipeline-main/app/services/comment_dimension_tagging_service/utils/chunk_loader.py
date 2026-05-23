"""Utility for loading chunk comments."""
from typing import Dict, Any, List, Tuple
from .comment_loader import load_comments_from_profiles
from ..job_status_manager import JobStatusManager


async def load_chunk_comments(
    storage_client, job_id: str, profiles: List[Any], profiles_chunk: List[Any],
    profile_ids_chunk: List[str], start_profile_index: int, enterprise_name: str
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """
    Load comments for the chunk.
    
    For first chunk (start_profile_index == 0):
    - Load all comments from all profiles to get total count
    - Then extract only the chunk's comments
    - This allows us to set total_comments in job status
    
    For subsequent chunks:
    - Load only the chunk's profiles (more efficient)
    """
    if start_profile_index == 0:
        # First chunk: load all to get total count
        JobStatusManager.mark_job_processing(job_id, enterprise_name)
        all_comments_full, profile_comments_map_full = await load_comments_from_profiles(
            storage_client, profiles
        )
        JobStatusManager.update_total_comments(job_id, len(all_comments_full), enterprise_name)
        
        # Extract only this chunk's comments
        profile_comments_map_chunk = {
            pid: profile_comments_map_full[pid]
            for pid in profile_ids_chunk if pid in profile_comments_map_full
        }
        all_comments_chunk = []
        for pid in profile_ids_chunk:
            if pid in profile_comments_map_chunk:
                all_comments_chunk.extend(profile_comments_map_chunk[pid]["comments"])
    else:
        # Subsequent chunks: load only this chunk's profiles
        all_comments_chunk, profile_comments_map_chunk = await load_comments_from_profiles(
            storage_client, profiles_chunk
        )
    
    return all_comments_chunk, profile_comments_map_chunk
