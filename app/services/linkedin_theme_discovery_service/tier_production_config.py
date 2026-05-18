"""
Tier-1 (authored posts) vs tier-2 (member interaction text) production defaults.

CLI: omitted optional numeric flags are filled in ``merge_cli_with_tier_profile`` before the run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class LlmRetryConfig:
    max_attempts: int = 5
    base_delay_sec: float = 1.0
    max_delay_sec: float = 60.0
    jitter_sec: float = 0.3


@dataclass(frozen=True)
class TierPipelineConfig:
    default_max_rounds: int
    default_map_max_output: int
    default_subsequent_rounds_max_themes: int
    default_min_categories: int
    default_max_categories: int
    default_map_post_partitions: int
    default_safety_margin: int
    llm_per_attempt_timeout: float
    client_total_timeout: float


# Tier 1: authored posts
TIER1_PIPELINE = TierPipelineConfig(
    default_max_rounds=512,
    default_map_max_output=2048,
    default_subsequent_rounds_max_themes=5,
    default_min_categories=5,
    default_max_categories=7,
    default_map_post_partitions=5,
    default_safety_margin=2048,
    llm_per_attempt_timeout=120.0,
    client_total_timeout=300.0,
)

# Tier 2: interaction copy — more map headroom, tighter new-theme and category band defaults
TIER2_PIPELINE = TierPipelineConfig(
    default_max_rounds=768,
    default_map_max_output=2048,
    default_subsequent_rounds_max_themes=4,
    default_min_categories=4,
    default_max_categories=6,
    default_map_post_partitions=5,
    default_safety_margin=2048,
    llm_per_attempt_timeout=120.0,
    client_total_timeout=360.0,
)


def get_tier_config(is_tier2: bool) -> TierPipelineConfig:
    return TIER2_PIPELINE if is_tier2 else TIER1_PIPELINE


def llm_retry_for_tier(is_tier2: bool) -> LlmRetryConfig:
    if is_tier2:
        return LlmRetryConfig(
            max_attempts=6,
            base_delay_sec=1.0,
            max_delay_sec=90.0,
            jitter_sec=0.4,
        )
    return LlmRetryConfig(
        max_attempts=5,
        base_delay_sec=1.0,
        max_delay_sec=60.0,
        jitter_sec=0.35,
    )


def coalesce_tier_int(cli_value: Optional[int], tier_default: int) -> int:
    return tier_default if cli_value is None else cli_value


def build_metadata_tier_block(is_tier2: bool, cfg: TierPipelineConfig, retry: LlmRetryConfig) -> dict[str, Any]:
    return {
        "tier_profile": "tier2_interactions" if is_tier2 else "tier1_posts",
        "llm_transport": "app.services.ai_gateway_service (call_via_gateway; json_object)",
        "model_rotation_config_keys": (
            "linkedin_theme_discovery_tier2" if is_tier2 else "linkedin_theme_discovery_tier1"
        ),
        "resolved_defaults": {
            "max_rounds": cfg.default_max_rounds,
            "map_max_output": cfg.default_map_max_output,
            "subsequent_rounds_max_themes": cfg.default_subsequent_rounds_max_themes,
            "min_categories": cfg.default_min_categories,
            "max_categories": cfg.default_max_categories,
            "map_post_partitions": cfg.default_map_post_partitions,
            "safety_margin": cfg.default_safety_margin,
            "llm_per_attempt_timeout": cfg.llm_per_attempt_timeout,
            "client_total_timeout": cfg.client_total_timeout,
        },
        "llm_retry": {
            "max_attempts": retry.max_attempts,
            "base_delay_sec": retry.base_delay_sec,
            "max_delay_sec": retry.max_delay_sec,
            "jitter_sec": retry.jitter_sec,
        },
    }


def merge_cli_with_tier_profile(args: Any) -> None:
    """
    Fills None-valued pipeline CLI fields from the tier profile (1 vs 2).
    Call after argparse, before ``run()``.
    """
    is_t2 = getattr(args, "tier", "1") == "2"
    c = get_tier_config(is_t2)
    if args.safety_margin is None:
        args.safety_margin = c.default_safety_margin
    if args.map_max_output is None:
        args.map_max_output = c.default_map_max_output
    if args.map_post_partitions is None:
        args.map_post_partitions = c.default_map_post_partitions
    if args.max_rounds is None:
        args.max_rounds = c.default_max_rounds
    if args.subsequent_rounds_max_themes is None:
        args.subsequent_rounds_max_themes = c.default_subsequent_rounds_max_themes
    if args.min_categories is None:
        args.min_categories = c.default_min_categories
    if args.max_categories is None:
        args.max_categories = c.default_max_categories
