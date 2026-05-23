"""Errors raised by the SGO training scripts (vendored into production)."""


class SgoPipelineError(RuntimeError):
    """Fatal configuration or data error in the delta-method pipeline."""
