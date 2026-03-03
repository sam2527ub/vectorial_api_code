"""Process posts from Apify dataset, split by profile, upload to S3, update profiles. Part of User Observation Fetch."""
import logging
from typing import List, Optional, Dict, Any
from app import database
from app.utils.helpers import normalize_linkedin_url
from app.utils.s3_utils import upload_json_to_s3, extract_s3_key_from_url, fetch_json_from_s3, get_s3_key_for_audience

logger = logging.getLogger(__name__)


def classify_observation_type(item: Dict[str, Any]) -> str:
    """Classify a LinkedIn post as 'individual', 'repost', or 'reaction'.

    Rules (derived from Apify post-scraper JSON structure):
    - isActivity=True with activityDescription containing 'reposted' → repost
    - resharedPost present with non-empty outer text → reaction
    - resharedPost present without outer text → repost
    - Otherwise → individual
    """
    is_activity = item.get("isActivity", False)
    activity_desc = item.get("activityDescription", "") or ""

    if is_activity and "reposted" in activity_desc.lower():
        return "repost"

    if "resharedPost" in item:
        outer_text = (item.get("text") or "").strip()
        return "reaction" if outer_text else "repost"

    return "individual"


async def process_posts_and_update_profiles(
    dataset_client,
    job_id: str,
    audience_room_id: Optional[str] = None,
    linkedin_urls: Optional[List[str]] = None,
    enterprise_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Process posts from Apify dataset, split by profile, upload to S3, and update AudienceProfile table."""
    from app.config import s3_client, s3_bucket

    logger.info(f"process_posts_and_update_profiles called: job_id={job_id}, audience_room_id={audience_room_id}, enterprise_name={enterprise_name}")

    if not database.is_audience_db_available():
        logger.warning("Audience database not available, skipping profile updates")
        return {"posts_found": 0, "profiles_updated": 0, "profiles_missing": 0}

    if not s3_client or not s3_bucket:
        logger.error(f"S3 not configured! s3_client={s3_client is not None}, s3_bucket={s3_bucket}. Set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME environment variable.")
        return {"posts_found": 0, "profiles_updated": 0, "profiles_missing": 0}

    logger.info(f"S3 configured: bucket={s3_bucket}")

    # Fetch profiles - either from specific room or all profiles if URLs provided
    room_source = None
    if audience_room_id:
        logger.info(f"Fetching profiles for audience_room_id={audience_room_id}")
        room = database.find_audience_room_by_id(audience_room_id, include_profiles=True, enterprise_name=enterprise_name)
        profiles = room.profiles if room else []
        room_source = room.source if room else None
        logger.info(f"Found {len(profiles)} profiles in room {audience_room_id}, source={room_source}")
    elif linkedin_urls:
        logger.info(f"Fetching profiles matching {len(linkedin_urls)} LinkedIn URLs")
        all_profiles = database.find_audience_profiles(all_profiles=True, enterprise_name=enterprise_name)
        logger.info(f"Found {len(all_profiles)} total profiles, filtering by URLs...")
        normalized_scraped_urls = set()
        for url in linkedin_urls:
            norm_url = normalize_linkedin_url(url)
            if norm_url:
                normalized_scraped_urls.add(norm_url)
        profiles = []
        for p in all_profiles:
            norm_profile_url = normalize_linkedin_url(p.profileUrl)
            if norm_profile_url and norm_profile_url in normalized_scraped_urls:
                profiles.append(p)
        logger.info(f"Found {len(profiles)} matching profiles after filtering")
    else:
        logger.warning("No audience room ID or LinkedIn URLs provided, cannot match profiles")
        return {"posts_found": 0, "profiles_updated": 0, "profiles_missing": 0}

    if not profiles:
        logger.warning(f"No profiles found to match posts against (audience_room_id={audience_room_id}, linkedin_urls_count={len(linkedin_urls) if linkedin_urls else 0})")
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

    logger.info(f"Iterating through Apify dataset items for {len(profile_by_url)} profiles...")
    for item in dataset_client.iterate_items():
        total_items += 1
        if not isinstance(item, dict):
            continue
        input_url = normalize_linkedin_url(str(item.get("inputUrl", "")))
        if not input_url:
            continue
        target = profile_by_url.get(input_url)
        if target:
            item["observationType"] = classify_observation_type(item)
            posts_acc[target["id"]].append(item)

    profiles_with_posts = len([p for p in posts_acc.values() if p])
    logger.info(f"Processed {total_items} items from dataset, grouped into {profiles_with_posts} profiles with posts")

    updated = []
    missing = []
    room = None

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

        room_id = p["audienceRoomId"]
        source_to_use = room_source

        if not source_to_use and room_id:
            room = database.find_audience_room_by_id(
                room_id,
                include_profiles=False,
                enterprise_name=enterprise_name,
            )
            if room:
                source_to_use = room.source

        posts_key = get_s3_key_for_audience(
            room_id,
            f"profiles/{pid}/posts.json",
            enterprise_name,
            source_to_use,
        )
        room_source = None

        if room and room.id == room_id:
            room_source = room.source
        else:
            profile_room = database.find_audience_room_by_id(room_id, enterprise_name=enterprise_name)
            if profile_room:
                room_source = profile_room.source

        logger.info(f"Using source={room_source} for room_id={room_id} when generating S3 key for posts")
        posts_key = get_s3_key_for_audience(room_id, f"profiles/{pid}/posts.json", enterprise_name, room_source)
        logger.info(f"Uploading {len(posts_for_profile)} posts to S3 for profile {pid}: {posts_key}")

        try:
            posts_url = upload_json_to_s3(
                posts_key,
                {
                    "profile_id": pid,
                    "audience_room_id": room_id,
                    "linkedin_profile_url": p["linkedinUrl"],
                    "posts": posts_for_profile,
                },
            )
            logger.info(f"Successfully uploaded posts to S3: {posts_url}")
        except Exception as s3_error:
            logger.error(f"Failed to upload posts to S3 for profile {pid}: {s3_error}", exc_info=True)
            missing.append({
                "profile_id": pid,
                "profile_name": p["profileName"],
                "linkedin_url": p["linkedinUrl"],
                "reason": f"s3_upload_failed: {str(s3_error)}"
            })
            continue

        try:
            database.update_audience_profile(pid, {"postsS3Url": posts_url}, enterprise_name=enterprise_name)
            logger.info(f"Updated profile {pid} with postsS3Url: {posts_url}")
            updated.append({
                "profile_id": pid,
                "profile_name": p["profileName"],
                "linkedin_url": p["linkedinUrl"],
                "audience_room_id": room_id,
                "posts_s3_url": posts_url,
            })
        except Exception as e:
            logger.error(f"Error updating posts for profile {pid}: {e}", exc_info=True)
            missing.append({
                "profile_id": pid,
                "profile_name": p["profileName"],
                "linkedin_url": p["linkedinUrl"],
                "reason": "db_update_failed"
            })

    logger.info(f"Processing complete: {len(updated)} profiles updated, {len(missing)} profiles with issues, {total_items} total posts found")
    return {
        "posts_found": total_items,
        "profiles_updated": len(updated),
        "profiles_missing": len(missing),
        "updated": updated,
        "missing": missing,
    }
