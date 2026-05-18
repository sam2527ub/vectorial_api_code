#!/usr/bin/env python3
"""Deprecated alias — use ``run_linkedin_initial_prediction.py`` or the HTTP API."""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def main() -> None:
    warnings.warn(
        "run_linkedin_i0_only.py is deprecated; use run_linkedin_initial_prediction.py "
        "or POST /api/v1/audience-rooms/{id}/linkedin-initial-prediction/async",
        DeprecationWarning,
        stacklevel=1,
    )
    from run_linkedin_initial_prediction import main as _main

    _main()


if __name__ == "__main__":
    main()
