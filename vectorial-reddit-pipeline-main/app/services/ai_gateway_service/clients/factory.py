"""
Gateway Client Factory - Creates appropriate gateway handler based on provider.

This factory function enables easy switching between different gateway providers
by creating the appropriate client implementation based on configuration.
"""
from typing import Optional
from app.config import logger
from app.services.ai_gateway_service.clients.interface import GatewayHandlerInterface
from app.services.ai_gateway_service.clients.vercel_gateway_client import VercelGatewayClient


def create_gateway_handler(
    provider: str,
    openai_base_url: str,
    anthropic_base_url: str,
    base_url: Optional[str],
    api_key: str
) -> GatewayHandlerInterface:
    """
    Factory function to create appropriate gateway handler based on provider.
    """
    provider_lower = provider.lower()
    
    if provider_lower == "vercel":
        return VercelGatewayClient(
            openai_base_url=openai_base_url,
            anthropic_base_url=anthropic_base_url,
            api_key=api_key
        )
    elif provider_lower == "portkey":
        # Future implementation
        # from app.services.ai_gateway_service.clients.portkey_gateway_client import PortkeyGatewayClient
        # return PortkeyGatewayClient(...)
        raise NotImplementedError(f"Portkey gateway not yet implemented. Please use 'vercel' provider.")
    elif provider_lower == "langfuse":
        # Future implementation
        # from app.services.ai_gateway_service.clients.langfuse_gateway_client import LangfuseGatewayClient
        # return LangfuseGatewayClient(...)
        raise NotImplementedError(f"Langfuse gateway not yet implemented. Please use 'vercel' provider.")
    else:
        logger.warning(
            f"Unknown gateway provider '{provider}', defaulting to Vercel. "
            f"Supported providers: vercel (portkey, langfuse coming soon)"
        )
        return VercelGatewayClient(
            openai_base_url=openai_base_url,
            anthropic_base_url=anthropic_base_url,
            api_key=api_key
        )
