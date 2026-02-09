"""
Token Counter - Handles accurate token counting for AI models.

Provides token counting using tiktoken for accuracy or character-based estimation as fallback.
Supports model-specific encodings, caching for performance, and token-based text truncation.
"""
from typing import Optional, Any
from app.config import logger

# Try to import tiktoken for accurate token counting
try:
    import tiktoken
    TIKTOKEN_AVAILABLE = True
except ImportError:
    TIKTOKEN_AVAILABLE = False
    logger.warning("tiktoken not available - using character-based token estimation")


class TokenCounter:
    """Handles token counting for AI models using tiktoken or character estimation."""
    
    def __init__(self, config):
        """Initialize token counter with configuration."""
        self.config = config
        # Cache tokenizer encodings for performance
        self._tokenizer_encoding_cache = (
            {} if self.config.cache_tokenizer_encodings else None
        )
    
    def _get_tokenizer_encoding_for_model(self, model_name: str) -> Optional[Any]:
        """Get tiktoken encoding object for a specific model, with caching."""
        # Check if tiktoken should be used
        if not self.config.use_tiktoken_for_counting or not TIKTOKEN_AVAILABLE:
            return None
        
        # Normalize model name: remove provider prefix if present
        normalized_model_name = (
            model_name.split("/")[-1] 
            if "/" in model_name 
            else model_name
        )
        
        # Get encoding name from configuration mapping
        encoding_name = self.config.tokenizer_encodings.get(
            normalized_model_name, 
            self.config.tokenizer_encodings.get("default", "o200k_base")
        )
        
        # Use caching if enabled
        if self._tokenizer_encoding_cache is not None:
            if encoding_name not in self._tokenizer_encoding_cache:
                try:
                    self._tokenizer_encoding_cache[encoding_name] = (
                        tiktoken.get_encoding(encoding_name)
                    )
                except Exception as error:
                    logger.warning(
                        f"Could not get encoding for model {model_name}: {error}. "
                        f"Using character estimation."
                    )
                    return None
            return self._tokenizer_encoding_cache.get(encoding_name)
        else:
            # No caching - create encoding each time
            try:
                return tiktoken.get_encoding(encoding_name)
            except Exception as error:
                logger.warning(
                    f"Could not get encoding for model {model_name}: {error}. "
                    f"Using character estimation."
                )
                return None
    
    def count_tokens(self, text: str, model_name: str = "o3-mini") -> int:
        """Count the number of tokens in text using tiktoken or character estimation."""
        tokenizer_encoding = self._get_tokenizer_encoding_for_model(model_name)
        
        # Try accurate token counting with tiktoken
        if tokenizer_encoding:
            try:
                encoded_tokens = tokenizer_encoding.encode(text)
                return len(encoded_tokens)
            except Exception as error:
                logger.warning(
                    f"Token counting failed for model {model_name}: {error}. "
                    f"Using character estimation."
                )
        
        # Fallback: estimate tokens using character count
        estimated_tokens = len(text) // self.config.characters_per_token_estimate
        return estimated_tokens
    
    def truncate_to_tokens(self, text: str, max_tokens: int, model_name: str = "o3-mini") -> str:
        """Truncate text to fit within a maximum token count."""
        tokenizer_encoding = self._get_tokenizer_encoding_for_model(model_name)
        
        if tokenizer_encoding:
            try:
                # Encode content to tokens, truncate, then decode back to text
                content_tokens_list = tokenizer_encoding.encode(text)
                truncated_tokens_list = content_tokens_list[:max_tokens]
                truncated_content = tokenizer_encoding.decode(truncated_tokens_list)
                return truncated_content
            except Exception as error:
                logger.warning(
                    f"Token-based truncation failed: {error}. "
                    f"Using character-based truncation."
                )
        
        # Fallback: character-based truncation
        max_characters = max_tokens * self.config.characters_per_token_estimate
        return text[:max_characters]
