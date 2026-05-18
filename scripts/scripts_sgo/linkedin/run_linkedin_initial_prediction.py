#!/usr/bin/env python3
"""
Operator CLI for LinkedIn initial prediction (not part of the ``app`` package).

Uses ``linkedin_initial_prediction_room_runner`` in this directory (orchestration only);
core logic stays under ``app.services.linkedin_initial_prediction_service``.

Production traffic should use the chunked async HTTP API
(``POST .../linkedin-initial-prediction/async`` and poll status).

Usage (from repo root)::

  PYTHONPATH=. python scripts/scripts_sgo/linkedin/run_linkedin_initial_prediction.py \\
    --audience-room-id ROOM_ID --enterprise-name gamma --tier 1
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_LINKEDIN_SCRIPTS = Path(__file__).resolve().parent
if str(_LINKEDIN_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_LINKEDIN_SCRIPTS))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    p = argparse.ArgumentParser(description="LinkedIn initial prediction (S3). Use API for production.")
    p.add_argument("--audience-room-id", type=str, required=True)
    p.add_argument(
        "--enterprise-name",
        type=str,
        default=os.environ.get("DEFAULT_ENTERPRISE_NAME", "") or "",
    )
    p.add_argument("--source", type=str, default="")
    p.add_argument("--tier", type=int, default=1, choices=(1, 2))
    p.add_argument("--model", type=str, default=None)
    p.add_argument("--no-s3-upload", action="store_true")
    p.add_argument("--no-s3-partial", action="store_true")
    p.add_argument("--print-seed-context", action="store_true")
    p.add_argument("--debug-i0-prompt", action="store_true")
    args = p.parse_args()

    from linkedin_initial_prediction_room_runner import run_initial_prediction_for_linkedin_room

    async def _go():
        out = await run_initial_prediction_for_linkedin_room(
            args.audience_room_id.strip(),
            enterprise_name=args.enterprise_name or None,
            tier=args.tier,
            model=args.model,
            source=args.source or None,
            s3_upload=not args.no_s3_upload,
            no_s3_partial=args.no_s3_partial,
            print_seed_context=args.print_seed_context,
            debug_i0_prompt=args.debug_i0_prompt,
        )
        url = out.get("s3_url")
        if url:
            print(url, flush=True)
        logger.info("Done: %s", out)

    try:
        asyncio.run(_go())
    except KeyboardInterrupt:
        logger.warning("Interrupted.")
        sys.exit(130)


if __name__ == "__main__":
    main()
