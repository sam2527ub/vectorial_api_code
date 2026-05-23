"""Rate limiter for OpenAI API calls."""
import asyncio
import time
from typing import Optional
from collections import deque
from app.config import logger


class RateLimiter:
    """
    Rate limiter for tracking requests per minute (RPM) and tokens per minute (TPM).
    
    Ensures API calls stay within OpenAI rate limits by tracking request timestamps
    and token usage within a sliding time window.
    """
    
    def __init__(
        self,
        rpm_limit: int,
        tpm_limit: int,
        window_seconds: int = 60
    ):
        """
        Initialize rate limiter.
        
        Args:
            rpm_limit: Maximum requests per minute
            tpm_limit: Maximum tokens per minute
            window_seconds: Time window in seconds (default 60)
        """
        self.rpm_limit = rpm_limit
        self.tpm_limit = tpm_limit
        self.window_seconds = window_seconds
        
        # Track request timestamps
        self.request_timestamps: deque = deque()
        # Track token usage with timestamps
        self.token_usage: deque = deque()  # List of (timestamp, tokens) tuples
        
        # Lazy-initialize lock in async context (not in __init__ to avoid thread issues)
        self._lock: Optional[asyncio.Lock] = None 
        self._lock_init_lock = None
    def _ensure_lock(self):
        """Ensure lock is initialized. Must be called from async context."""
        if self._lock is None:
            # Thread-safe lazy initialization
            if self._lock_init_lock is None:
                import threading
                self._lock_init_lock = threading.Lock()
            
            with self._lock_init_lock:
                if self._lock is None:
                    self._lock = asyncio.Lock()

    async def wait_if_needed(self, estimated_tokens: int = 0) -> None:
        """
        Wait if rate limits would be exceeded.
        
        Args:
            estimated_tokens: Estimated tokens for this request
        """
        # Lazy-initialize lock in async context (has event loop)
        self._ensure_lock()
        async with self._lock:
            current_time = time.time()
            
            # Clean old entries outside the window
            cutoff_time = current_time - self.window_seconds
            
            # Remove old request timestamps
            while self.request_timestamps and self.request_timestamps[0] < cutoff_time:
                self.request_timestamps.popleft()
            
            # Remove old token usage
            while self.token_usage and self.token_usage[0][0] < cutoff_time:
                self.token_usage.popleft()
            
            # Check RPM limit
            if len(self.request_timestamps) >= self.rpm_limit:
                oldest_request = self.request_timestamps[0]
                wait_time = oldest_request + self.window_seconds - current_time + 0.1
                if wait_time > 0:
                    logger.debug(f"RPM limit reached, waiting {wait_time:.2f}s")
                    await asyncio.sleep(wait_time)
                    # Re-check after waiting
                    return await self.wait_if_needed(estimated_tokens)
            
            # Check TPM limit
            total_tokens = sum(tokens for _, tokens in self.token_usage)
            if total_tokens + estimated_tokens > self.tpm_limit:
                # Find when we can make the request
                if self.token_usage:
                    oldest_token_time = self.token_usage[0][0]
                    wait_time = oldest_token_time + self.window_seconds - current_time + 0.1
                    if wait_time > 0:
                        logger.debug(f"TPM limit would be exceeded, waiting {wait_time:.2f}s")
                        await asyncio.sleep(wait_time)
                        # Re-check after waiting
                        return await self.wait_if_needed(estimated_tokens)
            
            # Record this request
            self.request_timestamps.append(current_time)
            if estimated_tokens > 0:
                self.token_usage.append((current_time, estimated_tokens))
    
    async def record_usage(self, actual_tokens: int) -> None:
        """
        Record actual token usage after a request completes.
        
        Args:
            actual_tokens: Actual tokens used (input + output)
        """
        self._ensure_lock()
        async with self._lock:
            if self.token_usage:
                timestamp = self.token_usage[-1][0]
                self.token_usage[-1] = (timestamp, actual_tokens)