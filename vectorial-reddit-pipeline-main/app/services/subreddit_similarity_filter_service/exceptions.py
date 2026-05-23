"""Custom exceptions for subreddit similarity filtering."""


class AIRateLimitError(Exception):
    """Custom exception for AI provider rate limit errors."""
    pass


class AIContextLimitError(Exception):
    """Custom exception for AI provider context limit errors."""
    pass


# Backward compatibility aliases
GroqRateLimitError = AIRateLimitError
GroqContextLimitError = AIContextLimitError
