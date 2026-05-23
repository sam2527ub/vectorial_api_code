"""
Model Resolver - Resolves model names and retrieves context window sizes.

Normalizes model names (removes provider prefixes) and looks up context window sizes from
configuration. Performs fuzzy matching and falls back to default for unknown models.
"""
from app.config import logger
from app.services.dynamic_context_window_management_service.config import ContextConfig
from app.services.dynamic_context_window_management_service.utils.context_utils import normalize_model_name


class ModelResolver:
    """Handles model name resolution and context window lookup."""
    
    def __init__(self, config: ContextConfig):
        self.config = config
    
    def get_context_window_size(self, model_name: str) -> int:
        """Get the context window size (in tokens) for a specific model."""
        normalized_model_name = normalize_model_name(model_name)
        
        if normalized_model_name in self.config.model_context_windows:
            return self.config.model_context_windows[normalized_model_name]
        
        for model_key, window_size in self.config.model_context_windows.items():
            if model_key != "default" and (
                model_key in normalized_model_name 
                or normalized_model_name in model_key
            ):
                return window_size
        
        logger.warning(
            f"Unknown model '{model_name}', using default context window "
            f"({self.config.model_context_windows.get('default', 128000)} tokens)"
        )
        return self.config.model_context_windows.get("default", 128000)
