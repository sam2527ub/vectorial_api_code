"""
Production entry for the LinkedIn SGO delta-method pipeline (async).

Implementation lives under ``vendored_sgo/`` (vendored ``scripts/scripts_sgo``). Imports such as
``linkedin.*`` resolve only while the vendored root is on ``sys.path`` — production code never imports ``scripts/``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

from .linkedin_sgo_pipeline_async_config import (
    ensure_required_paths,
    ensure_sgo_namespace_defaults,
    get_default_namespace,
)
from .linkedin_sgo_pipeline_async_errors import LinkedInSGOPipelineError

logger = logging.getLogger(__name__)

VENDORED_SGO_ROOT = Path(__file__).resolve().parent / "vendored_sgo"


def _maybe_set_repo_root_env() -> None:
    if os.environ.get("AUDIENCE_WORKFLOW_ROOT", "").strip():
        return
    try:
        os.environ["AUDIENCE_WORKFLOW_ROOT"] = str(VENDORED_SGO_ROOT.parents[3])
    except Exception:
        pass


@contextmanager
def vendored_sgo_sys_path() -> Iterator[Path]:
    root = str(VENDORED_SGO_ROOT.resolve())
    if not VENDORED_SGO_ROOT.is_dir():
        raise LinkedInSGOPipelineError(f"Vendored SGO package missing: {VENDORED_SGO_ROOT}")
    inserted = False
    if root not in sys.path:
        sys.path.insert(0, root)
        inserted = True
    try:
        yield VENDORED_SGO_ROOT
    finally:
        if inserted:
            try:
                sys.path.remove(root)
            except ValueError:
                pass


async def run_linkedin_sgo_pipeline_async(args: Any) -> None:
    """
    Run the full pipeline (outer iterations, memory batches, refinement).

    ``args`` must match the vendored CLI namespace for ``linkedin/tier1_delta_method_predictions.py``.
    """
    ensure_required_paths(args)
    ensure_sgo_namespace_defaults(args)
    _maybe_set_repo_root_env()
    logger.info("[linkedin_sgo_pipeline_async] starting vendored delta-method run")
    try:
        with vendored_sgo_sys_path():
            import importlib

            import _package_setup  # noqa: F401

            mod = importlib.import_module("linkedin.tier1_delta_method_predictions")
            await mod.run_all(args)
    except SystemExit as e:
        code = e.code if e.code is not None else 1
        if code in (0, None):
            return
        raise LinkedInSGOPipelineError(f"SGO pipeline exited with status {code}") from e
    except LinkedInSGOPipelineError:
        raise
    except Exception as e:
        if e.__class__.__name__ == "SgoPipelineError":
            raise LinkedInSGOPipelineError(str(e)) from e
        raise


def run_linkedin_sgo_pipeline_async_sync(args: Any) -> None:
    asyncio.run(run_linkedin_sgo_pipeline_async(args))


def run_linkedin_sgo_from_yaml_overrides(overrides: Optional[dict] = None) -> None:
    ns = get_default_namespace(overrides)
    run_linkedin_sgo_pipeline_async_sync(ns)


# Back-compat aliases (older module name)
run_linkedin_sgo_delta_pipeline = run_linkedin_sgo_pipeline_async
run_linkedin_sgo_delta_pipeline_sync = run_linkedin_sgo_pipeline_async_sync

__all__ = (
    "VENDORED_SGO_ROOT",
    "run_linkedin_sgo_delta_pipeline",
    "run_linkedin_sgo_delta_pipeline_sync",
    "run_linkedin_sgo_from_yaml_overrides",
    "run_linkedin_sgo_pipeline_async",
    "run_linkedin_sgo_pipeline_async_sync",
    "vendored_sgo_sys_path",
)
