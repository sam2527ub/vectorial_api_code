"""Workspace root resolution when this package lives under ``app/services/.../vendored_sgo``."""

from __future__ import annotations

import os
from pathlib import Path


def repo_root() -> Path:
    env = os.environ.get("AUDIENCE_WORKFLOW_ROOT", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    # vendored_sgo/_paths.py → parents[3] == repository root (Audience-workflow)
    here = Path(__file__).resolve().parent
    return here.parents[3]
