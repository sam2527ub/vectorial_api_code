"""Retry helpers for transient PostgreSQL / pool errors (idle disconnect, server recycle, etc.)."""

from __future__ import annotations

import functools
import logging
import time
from typing import Any, Callable, Optional, TypeVar

import psycopg2

logger = logging.getLogger(__name__)

TRANSIENT_PSYCOPG2_ERRORS = (psycopg2.OperationalError, psycopg2.InterfaceError)

F = TypeVar("F", bound=Callable[..., Any])


def db_retry_sync() -> Callable[[F], F]:
    """Decorator for synchronous DB helpers; attempts/backoff from ``config/runtime.yaml``."""

    def deco(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            from app.runtime_settings import get_runtime_settings

            cfg = get_runtime_settings().database_retry
            max_attempts = max(1, int(cfg.max_attempts))
            base_delay_s = float(cfg.base_delay_s)

            last: Optional[BaseException] = None
            for attempt in range(max_attempts):
                try:
                    return fn(*args, **kwargs)
                except TRANSIENT_PSYCOPG2_ERRORS as e:
                    last = e
                    if attempt == max_attempts - 1:
                        logger.error(
                            "[DB_RETRY] exhausted fn=%s attempts=%s last_error=%s",
                            fn.__name__,
                            max_attempts,
                            e,
                        )
                        raise
                    logger.warning(
                        "[DB_RETRY] transient fn=%s attempt=%s/%s error=%s",
                        fn.__name__,
                        attempt + 1,
                        max_attempts,
                        e,
                    )
                    time.sleep(base_delay_s * (2**attempt))
            raise last  # pragma: no cover

        return wrapper  # type: ignore[return-value]

    return deco
