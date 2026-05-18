#!/usr/bin/env python3
"""
Deprecated entrypoint.

Tier text normalization runs inside the API when you call:

  POST /api/v1/audience-rooms/{audience_room_id}/rebuild-tiered-posts

Implementation lives in app.services.linkedin_post_tier_service.post_text_polish (clean_tiered_blob),
called from tier_builder before tier JSON is uploaded to S3.
"""

from __future__ import annotations

import sys


def main() -> None:
    print(
        "This script is deprecated. Use POST .../rebuild-tiered-posts so tier JSON is normalized on S3.",
        file=sys.stderr,
    )
    print(
        "See app/services/linkedin_post_tier_service/post_text_polish.py (clean_tiered_blob).",
        file=sys.stderr,
    )
    sys.exit(2)


if __name__ == "__main__":
    main()
