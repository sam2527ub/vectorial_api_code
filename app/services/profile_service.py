"""Profile processing service."""
import logging
from typing import List, Optional, Dict, Any
from app import database
from app.utils.helpers import normalize_linkedin_url
from app.utils.s3_utils import upload_json_to_s3, extract_s3_key_from_url, fetch_json_from_s3

logger = logging.getLogger(__name__)


async def process_posts_and_update_profiles(
    dataset_client,
    job_id: str,
    audience_room_id: Optional[str] = None,
    linkedin_urls: Optional[List[str]] = None,
    enterprise_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Process posts from Apify dataset, split by profile, upload to S3, and update AudienceProfile table.
    
    Args:
        dataset_client: Apify dataset client to iterate items from
        job_id: Scrape job ID
        audience_room_id: Optional audience room ID to filter profiles
        linkedin_urls: Optional list of LinkedIn URLs that were scraped (for matching)
        enterprise_name: Optional enterprise name (gamma, app, entelligence, beta). 
                        Defaults to AUDIENCE_DATABASE_URL if None.
    
    Returns:
        Dictionary with processing results (posts_found, profiles_updated, profiles_missing, etc.)
    """
    from app.config import s3_client, s3_bucket
    
    if not database.is_audience_db_available():
        logger.warning("Audience database not available, skipping profile updates")
        return {"posts_found": 0, "profiles_updated": 0, "profiles_missing": 0}
    
    if not s3_client or not s3_bucket:
        logger.warning("S3 not configured, skipping profile updates")
        return {"posts_found": 0, "profiles_updated": 0, "profiles_missing": 0}
    
    # Fetch profiles - either from specific room or all profiles if URLs provided
    if audience_room_id:
        # Get profiles from specific audience room using find_audience_room_by_id which supports enterprise_name
        room = database.find_audience_room_by_id(audience_room_id, include_profiles=True, enterprise_name=enterprise_name)
        profiles = room.profiles if room else []
    elif linkedin_urls:
        # Get all profiles that match any of the scraped URLs
        all_profiles = database.find_audience_profiles(all_profiles=True, enterprise_name=enterprise_name)
        # Normalize scraped URLs for matching
        normalized_scraped_urls = set()
        for url in linkedin_urls:
            norm_url = normalize_linkedin_url(url)
            if norm_url:
                normalized_scraped_urls.add(norm_url)
        
        # Filter profiles that match scraped URLs
        profiles = []
        for p in all_profiles:
            norm_profile_url = normalize_linkedin_url(p.profileUrl)
            if norm_profile_url and norm_profile_url in normalized_scraped_urls:
                profiles.append(p)
    else:
        # No room ID and no URLs - can't match profiles
        logger.warning("No audience room ID or LinkedIn URLs provided, cannot match profiles")
        return {"posts_found": 0, "profiles_updated": 0, "profiles_missing": 0}
    
    if not profiles:
        logger.warning(f"No profiles found to match posts against")
        return {"posts_found": 0, "profiles_updated": 0, "profiles_missing": 0}
    
    # Build profile lookup by normalized URL
    profile_by_url: Dict[str, Dict[str, Any]] = {}
    for p in profiles:
        norm_url = normalize_linkedin_url(p.profileUrl)
        if norm_url:
            profile_by_url[norm_url] = {
                "id": p.id,
                "profileName": p.profileName,
                "profileUrl": p.profileUrl,
                "linkedinUrl": p.profileUrl,
                "audienceRoomId": p.audienceRoomId,
            }
    
    # Group posts by profile
    posts_acc: Dict[str, List[Any]] = {p["id"]: [] for p in profile_by_url.values()}
    total_items = 0
    
    for item in dataset_client.iterate_items():
        total_items += 1
        if not isinstance(item, dict):
            continue
        input_url = normalize_linkedin_url(str(item.get("inputUrl", "")))
        if not input_url:
            continue
        target = profile_by_url.get(input_url)
        if target:
            posts_acc[target["id"]].append(item)
    
    # Upload posts to S3 and update database
    updated = []
    missing = []
    
    for p in profile_by_url.values():
        pid = p["id"]
        posts_for_profile = posts_acc.get(pid, [])
        if not posts_for_profile:
            missing.append({
                "profile_id": pid,
                "profile_name": p["profileName"],
                "linkedin_url": p["linkedinUrl"],
                "reason": "no_posts_found"
            })
            continue
        
        # Use the profile's audience room ID for S3 path
        room_id = p["audienceRoomId"]
        posts_key = f"audiences/{room_id}/profiles/{pid}/posts.json"
        posts_url = upload_json_to_s3(
            posts_key,
            {
                "profile_id": pid,
                "audience_room_id": room_id,
                "linkedin_profile_url": p["linkedinUrl"],
                "posts": posts_for_profile,
            },
        )
        
        try:
            database.update_audience_profile(pid, {"postsS3Url": posts_url}, enterprise_name=enterprise_name)
            updated.append({
                "profile_id": pid,
                "profile_name": p["profileName"],
                "linkedin_url": p["linkedinUrl"],
                "audience_room_id": room_id,
                "posts_s3_url": posts_url,
            })
        except Exception as e:
            logger.error(f"Error updating posts for profile {pid}: {e}")
            missing.append({
                "profile_id": pid,
                "profile_name": p["profileName"],
                "linkedin_url": p["linkedinUrl"],
                "reason": "db_update_failed"
            })
    
    return {
        "posts_found": total_items,
        "profiles_updated": len(updated),
        "profiles_missing": len(missing),
        "updated": updated,
        "missing": missing,
    }

