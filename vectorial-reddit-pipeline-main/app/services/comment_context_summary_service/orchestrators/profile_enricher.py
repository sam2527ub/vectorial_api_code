"""Orchestrator for enriching comments in a profile."""
import asyncio
from typing import Dict, Any, List
from app.config import logger
from app.utils.s3_utils import get_s3_key_for_audience
from app.services.comment_context_summary_service.config import CommentContextSummaryConfig
from app.services.comment_context_summary_service.clients.storage import get_storage_client
from app.services.comment_context_summary_service.clients.ai import get_ai_client
from app.services.comment_context_summary_service.utils.context_builder import build_context_for_comment


class ProfileEnricher:
    """Enriches comments for a single profile."""

    def __init__(self, config: CommentContextSummaryConfig, comment_processor=None):
        self.config = config
        self.storage_client = get_storage_client()
        self.ai_client = get_ai_client(config)
        self._comment_processor = comment_processor  # Shared processor for rate limiting

    def _get_comments_s3_key(
        self, room_id: str, profile_id: str, enterprise_name: str, source: str
    ) -> str:
        return get_s3_key_for_audience(
            room_id,
            f"profiles/{profile_id}/comments.json",
            enterprise_name,
            source,
        )

    async def enrich_profile_comments(
        self,
        profile_id: str,
        room_id: str,
        enterprise_name: str,
        source: str,
        post_url_to_items: Dict[str, List[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        """Enrich comments for a single profile."""
        try:
            comments_key = self._get_comments_s3_key(
                room_id, profile_id, enterprise_name, source
            )
            logger.debug(f"[Profile {profile_id}] Loading comments from S3: {comments_key}")
            # Read JSON from S3 in thread pool (storage client is synchronous)
            comments = await asyncio.to_thread(
                self.storage_client.read_json,
                comments_key
            )

            if not isinstance(comments, list):
                logger.warning(
                    f"[Profile {profile_id}] comments.json is not a list (type: {type(comments)})"
                )
                return {
                    "profile_id": profile_id,
                    "status": "error",
                    "error": "comments.json is not a list",
                    "comments_enriched": 0,
                    "comments_skipped": 0,
                }

            comments_to_enrich = comments[: self.config.comments_per_profile]
            logger.info(
                f"[Profile {profile_id}] Processing {len(comments_to_enrich)}/{len(comments)} comments "
                f"(limit: {self.config.comments_per_profile})"
            )

            # Use shared CommentProcessor (passed from handler) or create new one
            # This ensures shared rate limiter across all profiles
            if not hasattr(self, '_comment_processor') or self._comment_processor is None:
                from app.services.comment_context_summary_service.orchestrators.comment_processor import CommentProcessor
                self._comment_processor = CommentProcessor()
            
            results = await self._comment_processor.process_comments(
                comments_to_enrich, post_url_to_items, self.config
            )

            enriched_count = 0
            skipped_count = 0

            # Process results and update comments
            for result in results:
                if result.get("status") == "success":
                    enriched_count += 1
                    # Comment is already updated in-place by processor
                else:
                    skipped_count += 1

            logger.debug(
                f"[Profile {profile_id}] Saving enriched comments to S3: {comments_key}"
            )
            # Write JSON to S3 in thread pool (storage client is synchronous)
            await asyncio.to_thread(
                self.storage_client.write_json,
                comments_key,
                comments
            )

            logger.info(
                f"[Profile {profile_id}] Enrichment complete: {enriched_count} enriched, "
                f"{skipped_count} skipped"
            )

            return {
                "profile_id": profile_id,
                "status": "success",
                "comments_enriched": enriched_count,
                "comments_skipped": skipped_count,
            }

        except Exception as e:
            logger.error(
                f"[Profile {profile_id}] Error enriching comments: {e}", exc_info=True
            )
            return {
                "profile_id": profile_id,
                "status": "error",
                "error": str(e),
                "comments_enriched": 0,
                "comments_skipped": 0,
            }
