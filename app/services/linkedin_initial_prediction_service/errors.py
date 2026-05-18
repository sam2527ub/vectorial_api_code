"""Errors for LinkedIn initial prediction (S3 production pipeline)."""


class InitialPredictionError(Exception):
    """Raised when initial prediction cannot run (missing S3, wrong audience type, etc.)."""
