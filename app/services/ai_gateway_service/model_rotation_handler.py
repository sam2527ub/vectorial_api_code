"""Model rotation utilities for gateway client."""
from typing import List, Optional, Tuple, Callable, Any
from app.config import logger
from app.services.ai_gateway_service.utils import gateway_utils


def resolve_model_config(
    config: Any,
    default_model: Optional[str],
    fallback_models: Optional[List[str]],
    config_default_attr: str,
    config_fallbacks_attr: str,
    hardcoded_default: str
) -> Tuple[str, List[str]]:
    """Resolve default model and fallbacks from parameters or config."""
    if default_model is None:
        default_model = (
            getattr(config, config_default_attr, None)
            if hasattr(config, config_default_attr)
            else hardcoded_default
        )
    
    if fallback_models is None:
        fallback_models = (
            getattr(config, config_fallbacks_attr, [])
            if hasattr(config, config_fallbacks_attr)
            else []
        )
    
    return default_model, fallback_models


def prepare_model_rotation(
    model: Optional[str],
    default_model: str,
    fallback_models: List[str],
    provider: str,
    legacy_model_handler: Optional[Callable[[Optional[str], str], Optional[str]]] = None
) -> Tuple[str, List[str]]:
    """Prepare model rotation list: normalize models and remove duplicates."""
    if legacy_model_handler:
        model = legacy_model_handler(model, default_model)
    
    effective_model = model or default_model
    normalized_primary = gateway_utils.normalize_model_name(effective_model, provider)
    
    normalized_fallbacks = [
        gateway_utils.normalize_model_name(m, provider) 
        for m in fallback_models
    ]
    fallback_models_clean = [m for m in normalized_fallbacks if m != normalized_primary]
    
    models_to_try = [normalized_primary] + fallback_models_clean
    
    if not models_to_try:
        raise ValueError("At least one model must be provided")
    
    primary_model = models_to_try[0]
    fallback_models_final = models_to_try[1:] if len(models_to_try) > 1 else []
    
    return primary_model, fallback_models_final


def log_model_rotation(
    context_id: str,
    primary_model: str,
    fallback_models: List[str]
) -> None:
    """Log model rotation information."""
    if fallback_models:
        models_to_try = [primary_model] + fallback_models
        providers = [gateway_utils.get_model_provider(m) for m in models_to_try]
        logger.debug(
            f"Context {context_id}: Cross-provider model rotation - Primary: {primary_model}, "
            f"Fallbacks: {fallback_models} (providers: {providers})"
        )
    else:
        primary_provider = gateway_utils.get_model_provider(primary_model)
        logger.debug(f"Context {context_id}: Using model {primary_model} (provider: {primary_provider})")
