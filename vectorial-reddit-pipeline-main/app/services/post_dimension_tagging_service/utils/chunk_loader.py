"""Utility for loading chunk posts."""
from typing import Dict, Any, List, Tuple
from ..utils.post_loader import load_posts_from_profiles
from ..job_status_manager import JobStatusManager


async def load_chunk_posts(
    storage_client, job_id: str, profiles: List[Any], profiles_chunk: List[Any],
    profile_ids_chunk: List[str], start_profile_index: int, enterprise_name: str
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """Load posts for the chunk."""
    if start_profile_index == 0:
        JobStatusManager.mark_job_processing(job_id, enterprise_name)
        all_posts_full, profile_posts_map_full = await load_posts_from_profiles(storage_client, profiles)
        JobStatusManager.update_total_posts(job_id, len(all_posts_full), enterprise_name)
        
        profile_posts_map_chunk = {
            pid: profile_posts_map_full[pid]
            for pid in profile_ids_chunk if pid in profile_posts_map_full
        }
        all_posts_chunk = []
        for pid in profile_ids_chunk:
            if pid in profile_posts_map_chunk:
                all_posts_chunk.extend(profile_posts_map_chunk[pid]["posts"])
    else:
        all_posts_chunk, profile_posts_map_chunk = await load_posts_from_profiles(storage_client, profiles_chunk)
    
    return all_posts_chunk, profile_posts_map_chunk
