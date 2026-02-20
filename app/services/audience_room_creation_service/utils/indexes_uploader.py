"""Upload audience room indexes/search results JSON to S3 when provided."""
from datetime import datetime
from typing import Any, List, Optional

from app.config import logger
from app.utils.s3_utils import get_s3_key_for_audience, upload_json_to_s3


def upload_audience_indexes_if_present(
    room_id: str,
    query: Optional[str],
    search_results: Optional[List[Any]],
    enterprise_name: Optional[str],
    source: Optional[str],
) -> Optional[str]:
    """
    If query and search_results are present, upload indexes.json to S3 and return its URL.
    Otherwise return None.
    """
    if not query or not search_results:
        return None
    try:
        indexes_data = {
            "audience_room_id": room_id,
            "query": query,
            "total_results": len(search_results),
            "results": search_results,
            "timestamp": datetime.utcnow().isoformat(),
        }
        key = get_s3_key_for_audience(room_id, "indexes.json", enterprise_name, source)
        return upload_json_to_s3(key, indexes_data)
    except Exception as e:
        logger.error(f"Failed to upload indexes for room_id={room_id}: {e}", exc_info=True)
        return None
