"""Gateway clients (vectorial-reddit-pipeline style)."""
from .interface import GatewayHandlerInterface
from .factory import create_gateway_handler

__all__ = ["GatewayHandlerInterface", "create_gateway_handler"]
