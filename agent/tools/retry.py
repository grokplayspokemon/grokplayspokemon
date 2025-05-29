"""
Retry decorator utilities for GrokAgent.

This module provides a decorator factory to automatically retry a function
when it raises an exception, handling transient errors without failing
the entire agent iteration.

IMPORTANT:
  - This is NOT part of the official xAI documentation or SDK.
  - It is custom helper code to improve robustness against network glitches.
  - Retries always occur within a single call to GrokAgent.get_action(), so
    they do not count as separate agent iterations.
  - After exceeding `max_attempts`, the last exception is propagated.
"""
import time
import functools

def retry_on_exception(max_attempts=3, delay=1.0, exceptions=(Exception,)):
    """
    Decorator factory to retry a function call when it raises an exception.

    Parameters:
      max_attempts (int): total number of attempts (including the first call).
      delay (float): seconds to wait between retry attempts.
      exceptions (tuple): exception types that should trigger a retry.

    Usage:
      @retry_on_exception(max_attempts=3, delay=2.0)
      def call_api(...):
          ...

    Behavior:
      - The decorated function is called up to max_attempts times.
      - If it succeeds, returns its result immediately.
      - If it raises one of the specified exceptions, waits `delay` seconds
        and retries until attempts are exhausted.
      - If all attempts fail, re-raises the final exception.
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            attempts = 0
            while True:
                try:
                    return fn(*args, **kwargs)
                except exceptions as e:
                    attempts += 1
                    if attempts >= max_attempts:
                        raise
                    time.sleep(delay)
        return wrapper
    return decorator 