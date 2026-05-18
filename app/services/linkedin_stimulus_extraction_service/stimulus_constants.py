"""Batched OpenAI calls; JSON object mode; match legacy script scale."""

from __future__ import annotations

# Mirror scripts/LInkedin_.../extract_stimulus_category.py
BATCH_SIZE = 5
# Tier-1: many posts in batch; tier-2: category from reply text only
TIER1_COMPLETION_MAX_TOKENS = 4096
TIER2_COMPLETION_MAX_TOKENS = 4096
STIMULUS_JSON_FORMAT = {"type": "json_object"}
STIMULUS_LLM_TEMPERATURE = 0.25

# Legacy pricing for metadata only (not billed here)
PRICE_INPUT_1M = 0.15
PRICE_OUTPUT_1M = 0.60

# Gateway config attr names
_T1D = "linkedin_stimulus_extraction_tier1_default"
_T1F = "linkedin_stimulus_extraction_tier1_fallbacks"
_T2D = "linkedin_stimulus_extraction_tier2_default"
_T2F = "linkedin_stimulus_extraction_tier2_fallbacks"

__all__ = (
    "BATCH_SIZE",
    "PRICE_INPUT_1M",
    "PRICE_OUTPUT_1M",
    "STIMULUS_JSON_FORMAT",
    "STIMULUS_LLM_TEMPERATURE",
    "TIER1_COMPLETION_MAX_TOKENS",
    "TIER2_COMPLETION_MAX_TOKENS",
    "_T1D",
    "_T1F",
    "_T2D",
    "_T2F",
)
