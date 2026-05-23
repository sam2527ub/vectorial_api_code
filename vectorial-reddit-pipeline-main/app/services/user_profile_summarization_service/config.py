"""Configuration for User Profile Summarization service."""
from typing import List, Optional
from app.config import logger
from app.services.ai_gateway_service import ai_gateway


class SummarizationConfig:
    """Configuration for profile summarization."""
    
    def __init__(self):
        """Initialize summarization configuration."""
        self.default_model, self.fallback_models = self._get_model_config()
        self.max_completion_tokens_single = 1500
        self.max_completion_tokens_batch = 1000
        self.batch_delay_seconds = 0.8
        self.max_keywords = 15
        self.max_highlights = 6
        self.max_comments_limit = 40
    
    def _get_model_config(self) -> tuple[str, List[str]]:
        """Get default model and fallbacks from gateway config or use defaults."""
        if ai_gateway.enabled:
            gateway_config = ai_gateway.config
            if hasattr(gateway_config, 'profile_summary_default'):
                default = gateway_config.profile_summary_default
                fallbacks = gateway_config.profile_summary_fallbacks
                return default, fallbacks
        
        # Fallback defaults for profile summarization
        return "openai/gpt-5-mini", [
            "anthropic/claude-sonnet-4.5",
            "openai/gpt-4o-mini"
        ]
    
    def get_model(self, model: Optional[str] = None) -> str:
        """Get model to use, with fallback to default."""
        return model or self.default_model
