"""
Gateway Configuration - Loads and manages AI Gateway configuration.
"""
import os
import yaml
from pathlib import Path
from app.config import logger


class GatewayConfig:
    """Loads AI Gateway config from YAML and env vars (env vars override YAML)."""
    
    def __init__(self):
        """Initialize gateway config from YAML file and environment variables."""
        # Load YAML config file
        config_path = Path(__file__).parent / "config.yaml"
        yaml_config = self._load_yaml_config(config_path)
        
        # Gateway settings (env vars take precedence)
        self.base_url = os.getenv("AI_GATEWAY_BASE_URL", yaml_config.get("gateway", {}).get("base_url", ""))
        self.api_key = os.getenv("AI_GATEWAY_API_KEY", yaml_config.get("gateway", {}).get("api_key", ""))
        
        use_gateway_yaml = yaml_config.get("gateway", {}).get("enabled", False)
        use_gateway_env = os.getenv("USE_AI_GATEWAY", "").lower() == "true"
        self.use_gateway = use_gateway_env or use_gateway_yaml
        
        self.provider = os.getenv("GATEWAY_PROVIDER", yaml_config.get("gateway", {}).get("provider", "vercel")).lower()
        
        # Vercel AI Gateway default URLs
        vercel_config = yaml_config.get("gateway", {}).get("vercel", {})
        self.vercel_openai_url = vercel_config.get("openai_url", "https://ai-gateway.vercel.sh/v1")
        self.vercel_anthropic_url = vercel_config.get("anthropic_url", "https://ai-gateway.vercel.sh")
        
        # Model fallback configuration (env vars take precedence)
        # Gateway will automatically try different providers for each model
        rotation_config = yaml_config.get("model_rotation", {})
        rotation_str_env = os.getenv("OPENAI_MODEL_ROTATION", "")
        
        # Profile Summary config
        profile_config = rotation_config.get("profile_summary", {})
        self.profile_summary_default = profile_config.get("default", "openai/gpt-5-mini")
        self.profile_summary_fallbacks = profile_config.get("fallbacks", [
            "anthropic/claude-sonnet-4.5",
            "openai/gpt-4o-mini"
        ])
        
        # Group Summary config
        group_config = rotation_config.get("group_summary", {})
        self.group_summary_default = group_config.get("default", "anthropic/claude-sonnet-4.5")
        self.group_summary_fallbacks = group_config.get("fallbacks", [
            "openai/gpt-5-mini",
            "openai/gpt-4o-mini"
        ])
        
        # Dimension Tagging config (OpenAI models only - logprobs support required)
        dimension_config = rotation_config.get("dimension_tagging", {})
        self.dimension_tagging_default = dimension_config.get("default", "openai/gpt-4o-mini")
        self.dimension_tagging_fallbacks = dimension_config.get("fallbacks", [
            "openai/gpt-4o",
            "openai/gpt-4o-mini",
            "openai/gpt-3.5-turbo"
        ])

        # Subreddit Similarity config (Groq default for semantic similarity)
        subreddit_config = rotation_config.get("subreddit_similarity", {})
        self.subreddit_similarity_default = subreddit_config.get("default", "groq/llama-3.3-70b-versatile")
        self.subreddit_similarity_fallbacks = subreddit_config.get("fallbacks", [
            "openai/gpt-4o-mini",
            "anthropic/claude-sonnet-4.5"
        ])
        
        # Comment Context Summary config (Groq default for fast summarization)
        comment_context_config = rotation_config.get("comment_context_summary", {})
        self.comment_context_summary_default = comment_context_config.get("default", "groq/llama-3.3-70b-versatile")
        self.comment_context_summary_fallbacks = comment_context_config.get("fallbacks", [
            "openai/gpt-4o-mini",
            "openai/gpt-5-mini"
        ])
        
        # Legacy: general fallbacks (for backward compatibility)
        if rotation_str_env:
            # Parse from environment variable (comma-separated)
            self.model_fallbacks = [m.strip() for m in rotation_str_env.split(",") if m.strip()]
        else:
            # Use from YAML file
            self.model_fallbacks = rotation_config.get("models", [
                "openai/gpt-5-mini",
                "anthropic/claude-sonnet-4.5",
                "openai/gpt-4o-mini"
            ])
    
    def _load_yaml_config(self, config_path: Path) -> dict:
        """Load YAML config file. Returns empty dict if file missing or fails to load."""
        try:
            if config_path.exists():
                with open(config_path, 'r') as f:
                    return yaml.safe_load(f) or {}
            return {}
        except Exception as e:
            # If YAML loading fails, return empty dict (will use defaults)
            logger.warning(f"Could not load YAML config: {e}")
            return {}
