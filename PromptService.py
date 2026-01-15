import time
import logging
from typing import Any, Optional, Dict
from langsmith import Client

# Use standard logging if src.config.logging_config is not available
try:
    from src.config.logging_config import logger
except ImportError:
    # Fallback to standard logging
    logger = logging.getLogger(__name__)

class FallbackPrompt:
    """Simple prompt wrapper that supports .format() method like LangSmith prompts."""
    def __init__(self, template: str):
        self.template = template
    
    def format(self, **kwargs) -> str:
        return self.template.format(**kwargs)
    
    def __str__(self) -> str:
        return self.template

class PromptService:
    def __init__(self, api_key: str, cache_duration: int = 600, fallback_prompts: Optional[Dict[str, str]] = None):
        self.client = Client(api_key=api_key)
        self.cache_duration = cache_duration
        self._cache = {}
        self._last_fetched = {}
        self.fallback_prompts = fallback_prompts or {}
    
    def get_prompt(self, prompt_name: str) -> Any:
        current_time = time.time()
        
        if (prompt_name in self._cache and 
            prompt_name in self._last_fetched and 
            current_time - self._last_fetched[prompt_name] < self.cache_duration):
            
            logger.info(f"Using CACHED prompt: {prompt_name} "
                       f"(cached {current_time - self._last_fetched[prompt_name]:.1f}s ago)")
            return self._cache[prompt_name]
        
        logger.info(f"Fetching FRESH prompt from LangSmith: {prompt_name}")
        try:
            prompt = self.client.pull_prompt(prompt_name, include_model=False)
            self._cache[prompt_name] = prompt
            self._last_fetched[prompt_name] = current_time
            logger.info(f"Successfully cached prompt: {prompt_name}")
            return prompt
        except Exception as e:
            logger.error(f"Failed to fetch prompt '{prompt_name}' from LangSmith: {e}")
            
            # Try fallback if available
            if prompt_name in self.fallback_prompts:
                logger.warning(f"Using FALLBACK prompt for '{prompt_name}' (LangSmith unavailable)")
                # Create a FallbackPrompt wrapper that supports .format()
                fallback_prompt = FallbackPrompt(self.fallback_prompts[prompt_name])
                # Cache the fallback
                self._cache[prompt_name] = fallback_prompt
                self._last_fetched[prompt_name] = current_time
                return fallback_prompt
            
            raise RuntimeError(f"Failed to fetch prompt '{prompt_name}' from LangSmith and no fallback available: {e}")
    
    def clear_cache(self, prompt_name: Optional[str] = None) -> None:
        if prompt_name:
            self._cache.pop(prompt_name, None)
            self._last_fetched.pop(prompt_name, None)
            logger.info(f"Cleared cache for prompt: {prompt_name}")
        else:
            self._cache.clear()
            self._last_fetched.clear()
            logger.info("Cleared all prompt cache")
    
    def is_cached(self, prompt_name: str) -> bool:
        if prompt_name not in self._cache or prompt_name not in self._last_fetched:
            return False
        
        current_time = time.time()
        return current_time - self._last_fetched[prompt_name] < self.cache_duration


class CachedPrompt:
    def __init__(self, prompt_name: str, prompt_service: PromptService):
        self.prompt_name = prompt_name
        self.prompt_service = prompt_service
    
    def format(self, **kwargs) -> str:
        """Format the prompt with the given keyword arguments."""
        prompt = self.prompt_service.get_prompt(self.prompt_name)
        # If it's a FallbackPrompt, use its format method
        if isinstance(prompt, FallbackPrompt):
            return prompt.format(**kwargs)
        # If it's a LangSmith prompt, it should support format
        if hasattr(prompt, 'format'):
            return prompt.format(**kwargs)
        # Fallback: try to convert to string and format
        return str(prompt).format(**kwargs)
    
    def __getattr__(self, name):
        prompt = self.prompt_service.get_prompt(self.prompt_name)
        # If it's a FallbackPrompt, it already has the methods we need
        return getattr(prompt, name)
    
    def __call__(self, *args, **kwargs):
        prompt = self.prompt_service.get_prompt(self.prompt_name)
        # If it's a FallbackPrompt, it's not callable, but we can use .format()
        if isinstance(prompt, FallbackPrompt):
            return prompt.format(**kwargs) if kwargs else str(prompt)
        if callable(prompt):
            return prompt(*args, **kwargs)
        raise TypeError(f"'{self.prompt_name}' prompt is not callable")
    
    def __str__(self):
        prompt = self.prompt_service.get_prompt(self.prompt_name)
        return str(prompt)
