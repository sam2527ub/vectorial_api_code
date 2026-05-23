"""
Dynamic Context Window Manager Package - Module initialization and exports.

Initializes the package and provides a global context_manager instance for use throughout
the application. Exports main classes and configuration values.
"""

from app.services.dynamic_context_window_management_service.config import ContextConfig
from app.services.dynamic_context_window_management_service.context_manager import DynamicContextManager

# Create global context manager instance
context_manager = DynamicContextManager()

# Export commonly used config values for backward compatibility
_global_context_config = ContextConfig()
MODEL_CONTEXT_WINDOWS = _global_context_config.model_context_windows
DEFAULT_SAFETY_MARGIN = _global_context_config.safety_margin

__all__ = [
    "DynamicContextManager",
    "ContextConfig",
    "context_manager",
    "MODEL_CONTEXT_WINDOWS",
    "DEFAULT_SAFETY_MARGIN",
]
