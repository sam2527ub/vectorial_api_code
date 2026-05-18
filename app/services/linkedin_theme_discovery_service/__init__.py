"""
LinkedIn category/topic theme discovery (MAP → SHRINK → CATEGORIZE).

- **Prompts** — ``prompts/*.txt`` (per-tier map/categorize copy).
- **LLM** — ``app.services.ai_gateway`` + ``theme_llm_caller.call_theme_json_via_gateway``.
- **Production** — ``POST .../theme_category_discovery`` runs tier 1 then tier 2 in order (S3). No ``app`` code depends on ``scripts/``.
- **Local** — optional thin script under ``scripts/`` is not used by the API; defaults use ``local/linkedin_theme_discovery/`` in the repo.
"""

from . import theme_discovery_prompts
from .linkedin_theme_discovery_request_handler import (
    LinkedinThemeDiscoveryRequestHandler,
    request_handler,
)
from .pipeline import ThemeDiscoveryPipelineError, main, run
from .room_theme_discovery_runner import (
    run_linkedin_theme_discovery_both_tiers_for_room,
    run_linkedin_theme_discovery_for_room,
)
from .theme_llm_caller import call_theme_json_via_gateway
from .tier_production_config import (
    TIER1_PIPELINE,
    TIER2_PIPELINE,
    LlmRetryConfig,
    build_metadata_tier_block,
    get_tier_config,
    llm_retry_for_tier,
    merge_cli_with_tier_profile,
)

__all__ = (
    "LlmRetryConfig",
    "LinkedinThemeDiscoveryRequestHandler",
    "TIER1_PIPELINE",
    "TIER2_PIPELINE",
    "ThemeDiscoveryPipelineError",
    "build_metadata_tier_block",
    "call_theme_json_via_gateway",
    "get_tier_config",
    "llm_retry_for_tier",
    "main",
    "merge_cli_with_tier_profile",
    "request_handler",
    "run",
    "run_linkedin_theme_discovery_both_tiers_for_room",
    "run_linkedin_theme_discovery_for_room",
    "theme_discovery_prompts",
)
