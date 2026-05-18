"""
Rebuild LinkedIn post tier files (tier 1/2/3) in S3 for an audience room.

Requires the same environment as the API (AUDIENCE_* database URL, S3 bucket, AWS creds).
Run from the repository root, for example:

  PYTHONPATH=. python scripts/Linkedin_Posts_Extraction/extract_tiers.py ROOM_UUID --enterprise beta

Tier artifacts are written to:
  {enterprise}/linkedin-audience/{room_id}/tiered_posts/
  (tier_1_authored.json, tier_2_reposts_and_comments.json, tier_3_sparse.json, manifest.json)

In the API, the same logic is exposed as
POST /api/v1/audience-rooms/{audience_room_id}/rebuild-tiered-posts
(typically after group summary and/or scrapes). This CLI is for manual/ops runs
without starting the HTTP server.

See app.services.linkedin_post_tier_service.tier_builder for layout and semantics.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "audience_room_id",
        help="Audience room UUID (profiles must already have posts.json / comment.json in S3)",
    )
    parser.add_argument(
        "--enterprise",
        default=None,
        help="Enterprise segment for DB/S3 (e.g. beta, gamma); omit for default",
    )
    args = parser.parse_args()

    from app.config import initialize_clients

    initialize_clients()

    from app.services.linkedin_post_tier_service import rebuild_linkedin_post_tiers_for_room

    result = rebuild_linkedin_post_tiers_for_room(
        audience_room_id=args.audience_room_id.strip(),
        enterprise_name=args.enterprise,
    )
    if result is None:
        print("Tier rebuild returned None (check logs: DB, S3, or room not found).", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
