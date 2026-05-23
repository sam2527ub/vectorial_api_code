"""Profile summary utilities - fetching and combining summaries from S3."""
from typing import List, Dict, Any
from app.config import logger
from app.utils.s3_utils import extract_s3_key_from_url, fetch_json_from_s3
from app.services.audience_group_summarization_service.clients.storage.factory import get_storage_client


def _flatten_reddit_activity_snippet(
    data: Any,
    *,
    kind: str,
    max_chars: int,
    max_items: int = 100,
    max_item_chars: int = 1500,
) -> str:
    """
    Flatten scraped Reddit posts or comments into a text snippet that can be
    fed to a group-summary prompt. Preserves title+body for posts and body
    for comments. Used by the preview Vercel Workflow which skips LLM
    per-profile summaries.
    """
    items: List[Any] = []
    if isinstance(data, dict):
        items = data.get(kind + "s") or data.get("data") or data.get("items") or []
    elif isinstance(data, list):
        items = data

    chunks: List[str] = []
    total = 0
    for item in items[:max_items]:
        if not isinstance(item, dict):
            continue
        if kind == "post":
            title = (item.get("title") or "").strip()
            body = (
                item.get("body")
                or item.get("selftext")
                or item.get("text")
                or item.get("content")
                or ""
            )
            if not isinstance(body, str):
                body = ""
            combined = (f"{title}\n{body}" if title else body).strip()
        else:  # comment
            combined = (
                item.get("body")
                or item.get("text")
                or item.get("content")
                or ""
            )
            if isinstance(combined, str):
                combined = combined.strip()
            else:
                combined = ""

        if not combined:
            continue
        piece = combined[:max_item_chars]
        if total + len(piece) > max_chars:
            break
        chunks.append(piece)
        total += len(piece)
    return "\n---\n".join(chunks)


class ProfileSummaryFetcher:
    """Fetches profile summaries from S3."""
    
    def __init__(self):
        """Initialize profile summary fetcher."""
        self.storage_client = get_storage_client()

    def fetch_preview_style_summaries(
        self,
        profiles: List[Any],
        max_posts_chars: int = 8000,
        max_comments_chars: int = 4000,
    ) -> tuple:
        """
        Build synthetic per-profile summaries directly from S3-stored posts.json
        and comments.json, without requiring LLM profile summaries. Used by the
        Preview-then-Real workflow's preview pipeline where per-profile LLM
        summarization is intentionally skipped to keep latency low.

        Reddit has no "about" section equivalent - we lean entirely on raw
        scraped text.
        """
        profile_summaries: List[Dict[str, str]] = []
        profiles_processed = 0
        profiles_skipped = 0
        s3_client = self.storage_client.get_raw_client()
        s3_bucket = self.storage_client.get_bucket_name()

        for profile in profiles:
            try:
                posts_block = ""
                if profile.postsS3Url:
                    pk = extract_s3_key_from_url(profile.postsS3Url)
                    if pk:
                        try:
                            posts_data = fetch_json_from_s3(pk, s3_client=s3_client, s3_bucket=s3_bucket)
                            posts_block = _flatten_reddit_activity_snippet(
                                posts_data, kind="post", max_chars=max_posts_chars
                            )
                        except Exception as e:
                            logger.warning(f"Profile {profile.id}: failed to load posts: {e}")

                comments_block = ""
                if profile.commentsS3Url:
                    ck = extract_s3_key_from_url(profile.commentsS3Url)
                    if ck:
                        try:
                            comments_data = fetch_json_from_s3(ck, s3_client=s3_client, s3_bucket=s3_bucket)
                            comments_block = _flatten_reddit_activity_snippet(
                                comments_data, kind="comment", max_chars=max_comments_chars
                            )
                        except Exception as e:
                            logger.warning(f"Profile {profile.id}: failed to load comments: {e}")

                pieces: List[str] = [f"Username: {profile.profileName}"]
                if posts_block:
                    pieces.append("Recent posts (scraped):\n" + posts_block)
                if comments_block:
                    pieces.append("Recent comments (scraped):\n" + comments_block)

                combined = "\n\n".join(pieces).strip()
                if not combined or (not posts_block and not comments_block):
                    logger.warning(
                        f"Profile {profile.id} has no scraped posts or comments, skipping preview summary"
                    )
                    profiles_skipped += 1
                    continue

                profile_summaries.append({
                    "name": profile.profileName,
                    "summary": combined,
                })
                profiles_processed += 1
            except Exception as e:
                logger.error(
                    f"Error building preview-style summary for profile {profile.id}: {e}"
                )
                profiles_skipped += 1
                continue

        stats = {
            "profiles_processed": profiles_processed,
            "profiles_skipped": profiles_skipped,
            "total_profiles": len(profiles),
        }
        return profile_summaries, stats
    
    def fetch_summaries(
        self,
        profiles: List[Any]
    ) -> tuple:
        """
        Fetch profile summaries from S3.
        """
        profile_summaries = []
        profiles_processed = 0
        profiles_skipped = 0
        
        for profile in profiles:
            try:
                if not profile.profileDescriptionS3Url:
                    logger.warning(f"Profile {profile.id} has no description URL, skipping")
                    profiles_skipped += 1
                    continue
                
                profile_key = extract_s3_key_from_url(profile.profileDescriptionS3Url)
                if not profile_key:
                    logger.warning(f"Profile {profile.id} has invalid description URL, skipping")
                    profiles_skipped += 1
                    continue
                
                profile_data = fetch_json_from_s3(
                    profile_key,
                    s3_client=self.storage_client.get_raw_client(),
                    s3_bucket=self.storage_client.get_bucket_name()
                )
                profile_summary = profile_data.get("summary")
                
                if not profile_summary:
                    logger.warning(f"Profile {profile.id} has no summary, skipping")
                    profiles_skipped += 1
                    continue
                
                profile_summaries.append({
                    "name": profile.profileName,
                    "summary": profile_summary,
                })
                
                profiles_processed += 1
                
            except Exception as e:
                logger.error(f"Error fetching profile {profile.id} description: {e}")
                profiles_skipped += 1
                continue
        
        stats = {
            "profiles_processed": profiles_processed,
            "profiles_skipped": profiles_skipped,
            "total_profiles": len(profiles)
        }
        
        return profile_summaries, stats


def combine_profile_summaries(profile_summaries: List[Dict[str, str]]) -> str:
    """Combine profile summaries into a single formatted text."""
    return "\n\n".join([
        f"{idx + 1}. {p['name']}:\n{p['summary']}"
        for idx, p in enumerate(profile_summaries)
    ])
