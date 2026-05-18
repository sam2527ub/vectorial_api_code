"""Main application package.

Avoid eager ``from app import database`` here: importing any ``app.*`` submodule would pull DB
clients (Prisma/pools) and slow or block tooling/tests that only need leaf modules.
"""

from __future__ import annotations

import importlib
from typing import Any

__all__ = ["database"]


def __getattr__(name: str) -> Any:
    if name == "database":
        return importlib.import_module("app.database")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

