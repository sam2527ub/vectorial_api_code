"""
Configuration Loader - Loads and manages Dynamic Context Window Manager settings.

Loads configuration from YAML file and environment variables (env vars take precedence).
Handles model context windows, tokenizer encodings, safety margins, and batching config.
"""
import os
import yaml
from pathlib import Path
from app.config import logger


class ContextConfig:
    """Configuration loader for Dynamic Context Window Manager."""
    
    def __init__(self):
        """Initialize configuration by loading from YAML file."""
        # Locate config file in same directory as this module
        config_file_path = Path(__file__).parent / "config.yaml"
        yaml_configuration = self._load_yaml_config_file(config_file_path)
        
        # Safety margin: tokens reserved for overhead
        safety_margin_from_env = os.getenv("CONTEXT_SAFETY_MARGIN")
        self.safety_margin = (
            int(safety_margin_from_env) 
            if safety_margin_from_env 
                else yaml_configuration.get("safety_margin", 2000)
        )
        
        # Model context window sizes
        self.model_context_windows = yaml_configuration.get("model_context_windows", {
            "gpt-5-mini": 400000,
            "o3-mini": 200000,
            "gpt-4o-mini": 128000,
            "gpt-4o": 128000,
            "gpt-4": 8192,
            "claude-sonnet-4-5-20250929": 1000000,
            "claude-sonnet-4.5": 1000000,
            "claude-opus-4": 200000,
            "claude-3-5-sonnet": 200000,
            "llama-3.3-70b-versatile": 131072,
            "llama-3.1-70b-versatile": 0,
            "default": 128000,
        })
        
        # Tokenizer encoding mappings
        self.tokenizer_encodings = yaml_configuration.get("tokenizer_encodings", {
            "gpt-5-mini": "o200k_base",
            "o3-mini": "o200k_base",
            "gpt-4o-mini": "o200k_base",
            "gpt-4o": "o200k_base",
            "gpt-4": "cl100k_base",
            "claude-sonnet-4-5-20250929": "cl100k_base",
            "claude-sonnet-4.5": "cl100k_base",
            "claude-opus-4": "cl100k_base",
            "claude-3-5-sonnet": "cl100k_base",
            "claude-3-opus": "cl100k_base",
            "claude-3-sonnet": "cl100k_base",
            "claude-3-haiku": "cl100k_base",
            "llama-3.3-70b-versatile": "cl100k_base",
            "llama-3.1-70b-versatile": "cl100k_base",
            "default": "o200k_base",
        })
        
        # Default model to use when model name is not specified
        self.default_model = (
            os.getenv("DEFAULT_CONTEXT_MODEL") 
            or yaml_configuration.get("default_model", "gpt-5-mini")
        )
        
        # Batching configuration
        batching_configuration = yaml_configuration.get("batching", {})
        self.minimum_activities_per_batch = batching_configuration.get("min_activities_per_batch", 1)
        self.batching_enabled = batching_configuration.get("enabled", True)
        self.log_batch_statistics = batching_configuration.get("log_batch_stats", True)
        
        # Token counting configuration
        token_counting_configuration = yaml_configuration.get("token_counting", {})
        self.use_tiktoken_for_counting = (
            token_counting_configuration.get("use_tiktoken", True) 
            and self._is_tiktoken_available()
        )
        self.characters_per_token_estimate = token_counting_configuration.get("chars_per_token", 4)
        self.cache_tokenizer_encodings = token_counting_configuration.get("cache_encodings", True)
    
    def _load_yaml_config_file(self, config_file_path: Path) -> dict:
        """Load configuration dictionary from YAML file."""
        try:
            if config_file_path.exists():
                with open(config_file_path, 'r') as config_file:
                    loaded_config = yaml.safe_load(config_file)
                    return loaded_config or {}
            return {}
        except Exception as error:
            logger.warning(f"Could not load YAML config from {config_file_path}: {error}")
            return {}
    
    @staticmethod
    def _is_tiktoken_available() -> bool:
        """Check if tiktoken is available for token counting."""
        try:
            import tiktoken
            return True
        except ImportError:
            return False
