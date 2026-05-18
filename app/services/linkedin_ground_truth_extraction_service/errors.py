"""Errors for LinkedIn per-topic ground-truth (LLM Yes/No + logprobs) extraction."""


class GroundTruthExtractionError(Exception):
    """Invalid inputs, missing S3 artifacts, or unrecoverable extraction errors."""
