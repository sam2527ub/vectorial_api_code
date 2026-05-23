"""Reliable delivery of Fargate worker completion webhooks."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_INITIAL_BACKOFF_S = 2.0


async def post_sgo_webhook_with_retries(
    *,
    webhook_url: str,
    payload: Dict[str, Any],
    secret: Optional[str] = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    initial_backoff_s: float = DEFAULT_INITIAL_BACKOFF_S,
    timeout_s: float = 120.0,
) -> Dict[str, Any]:
    """
    POST JSON to the API webhook until success or attempts exhausted.

    Returns ``{"ok": True, "status_code": int}`` or ``{"ok": False, "error": str, ...}``.
    """
    headers: Dict[str, str] = {}
    if secret:
        headers["X-SGO-Webhook-Secret"] = secret.strip()

    last_error = ""
    last_status: Optional[int] = None
    backoff = max(0.5, float(initial_backoff_s))

    async with httpx.AsyncClient(timeout=timeout_s) as client:
        for attempt in range(1, max(1, int(max_attempts)) + 1):
            try:
                resp = await client.post(webhook_url, json=payload, headers=headers)
                last_status = resp.status_code
                if 200 <= resp.status_code < 300:
                    logger.info(
                        "[SGO_WEBHOOK] delivered status=%s attempt=%s",
                        resp.status_code,
                        attempt,
                    )
                    return {"ok": True, "status_code": resp.status_code}
                last_error = (resp.text or "")[:500]
                logger.warning(
                    "[SGO_WEBHOOK] HTTP %s attempt=%s/%s body=%s",
                    resp.status_code,
                    attempt,
                    max_attempts,
                    last_error[:200],
                )
            except Exception as e:
                last_error = str(e)
                logger.warning(
                    "[SGO_WEBHOOK] POST failed attempt=%s/%s: %s",
                    attempt,
                    max_attempts,
                    e,
                    exc_info=True,
                )
            if attempt < max_attempts:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 60.0)

    return {
        "ok": False,
        "status_code": last_status,
        "error": last_error or "webhook delivery failed",
        "attempts": max_attempts,
    }
