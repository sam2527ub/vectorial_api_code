"""Gateway client factory (vectorial-reddit-pipeline style)."""
from typing import Optional

from app.config import logger

from .interface import GatewayHandlerInterface
from .vercel_gateway_client import VercelGatewayClient


def create_gateway_handler(
    provider: str,
    openai_base_url: str,
    anthropic_base_url: str,
    base_url: Optional[str],
    api_key: str,
) -> GatewayHandlerInterface:
    """Create the appropriate gateway handler for the given provider."""
    provider_lower = provider.lower()
    if provider_lower == "vercel":
        return VercelGatewayClient(
            openai_base_url=openai_base_url,
            anthropic_base_url=anthropic_base_url,
            api_key=api_key,
        )
    logger.warning(f"Unknown gateway provider '{provider}', defaulting to Vercel")
    return VercelGatewayClient(
        openai_base_url=openai_base_url,
        anthropic_base_url=anthropic_base_url,
        api_key=api_key,
    )
