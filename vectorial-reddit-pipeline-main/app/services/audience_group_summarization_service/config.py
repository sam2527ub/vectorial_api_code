"""Configuration for Group Summary Generation service."""
from typing import List, Optional
from app.config import logger
from app.services.audience_group_summarization_service.clients.storage.factory import get_storage_client
from fastapi import HTTPException
from app.services.ai_gateway_service import ai_gateway


class GroupSummaryConfig:
    """Configuration for group summary generation."""
    
    def __init__(self):
        """Initialize configuration."""
        self.default_model, self.fallback_models = self._get_model_config()
        self.group_summary_max_tokens = 1200
        self.traits_max_tokens = 8000  # Increased for large trait responses
        self.max_log_length = 2000
    
    def _get_model_config(self) -> tuple[str, List[str]]:
        """Get default model and fallbacks from gateway config or use defaults."""
        if ai_gateway.enabled:
            gateway_config = ai_gateway.config
            if hasattr(gateway_config, 'group_summary_default'):
                default = gateway_config.group_summary_default
                fallbacks = gateway_config.group_summary_fallbacks
                return default, fallbacks
        
        # Fallback defaults for group summary
        return "anthropic/claude-sonnet-4.5", [
            "openai/gpt-5-mini",
            "openai/gpt-4o-mini"
        ]
    
    def validate_dependencies(self):
        """Validate that required dependencies are available."""
        if not ai_gateway.enabled:
            raise HTTPException(
                status_code=503,
                detail="AI Gateway not enabled. Please configure AI_GATEWAY_API_KEY or ANTHROPIC_API_KEY."
            )
        storage_client = get_storage_client()
        if not storage_client.is_configured() or not storage_client.get_bucket_name():
            raise HTTPException(
                status_code=503,
                detail="S3 is not configured; set AUDIENCE_BUCKET_NAME or VECTOR_BUCKET_NAME."
            )
