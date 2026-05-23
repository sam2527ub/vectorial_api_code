"""
AI Gateway module initialization.

This module provides a global gateway client instance for routing OpenAI and Claude API requests
through an AI gateway (e.g., Vercel AI Gateway). The gateway handles model fallbacks, retries,
and provides a unified interface for making AI API calls. A singleton instance is created on
module import and can be reused across the application.
"""
from app.services.ai_gateway_service.ai_gateway_client_handler import AIGatewayClient

# Global gateway client instance (initialized on module import)
# This singleton instance is created once when the module is first imported
# and can be reused across the application
ai_gateway = AIGatewayClient()

__all__ = ["AIGatewayClient", "ai_gateway"]
