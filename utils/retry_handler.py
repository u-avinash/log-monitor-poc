"""Retry decorator with exponential backoff."""
import time
import functools
from typing import Callable, Type, Tuple
import logging

logger = logging.getLogger(__name__)


def retry_with_backoff(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exponential_base: float = 2.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    on_retry: Callable[[Exception, int], None] = None
):
    """
    Decorator to retry a function with exponential backoff.
    
    Args:
        max_retries: Maximum number of retry attempts
        base_delay: Initial delay in seconds
        max_delay: Maximum delay in seconds
        exponential_base: Base for exponential backoff calculation
        exceptions: Tuple of exception types to catch and retry
        on_retry: Optional callback function called on each retry
        
    Example:
        @retry_with_backoff(max_retries=3, base_delay=1.0)
        def my_function():
            # ... potentially failing code
            pass
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            retries = 0
            
            while True:
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    retries += 1
                    
                    if retries > max_retries:
                        logger.error(
                            f"Function {func.__name__} failed after {max_retries} retries: {e}"
                        )
                        raise
                    
                    # Calculate delay with exponential backoff
                    delay = min(base_delay * (exponential_base ** (retries - 1)), max_delay)
                    
                    logger.warning(
                        f"Function {func.__name__} failed (attempt {retries}/{max_retries}): {e}. "
                        f"Retrying in {delay:.1f}s..."
                    )
                    
                    # Call retry callback if provided
                    if on_retry:
                        try:
                            on_retry(e, retries)
                        except Exception as callback_error:
                            logger.error(f"Error in retry callback: {callback_error}")
                    
                    time.sleep(delay)
        
        return wrapper
    return decorator


class RetryableError(Exception):
    """Base exception class for retryable errors."""
    pass


class RateLimitError(RetryableError):
    """Exception for rate limit errors that should be retried."""
    pass


class TemporaryError(RetryableError):
    """Exception for temporary errors that should be retried."""
    pass
