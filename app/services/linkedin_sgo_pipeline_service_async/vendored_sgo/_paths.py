"""Repository root for scripts_sgo (local CLI) and vendored_sgo (production) layouts."""

from __future__ import annotations

import os
from pathlib import Path


def repo_root() -> Path:
    explicit = (os.environ.get("AUDIENCE_WORKFLOW_ROOT") or "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    here = Path(__file__).resolve().parent
    if here.name == "vendored_sgo":
        return here.parents[3]
    # scripts/scripts_sgo → parents[1] == Audience-workflow repo root
    return here.parents[1]
