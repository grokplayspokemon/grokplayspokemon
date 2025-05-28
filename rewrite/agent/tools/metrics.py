"""
Usage metrics utilities for GrokAgent.

This module provides functions to extract detailed token usage and cost metrics
from a xAI ChatCompletion response, including reasoning tokens when available.

After each API call, you can call `extract_usage_metrics(response)` to gather:
  - prompt_tokens: number of tokens in the input prompt
  - completion_tokens: number of tokens in the model's final output
  - total_tokens: sum of prompt and completion tokens
  - reasoning_tokens: number of tokens spent generating the reasoning trace (if present)
These metrics can help monitor cost and performance.
"""

def extract_usage_metrics(response):
    """
    Extract token usage metrics from a xAI ChatCompletion response object.

    Parameters:
      response: the raw response object returned by xAI client

    Returns:
      A dict with keys:
        - prompt_tokens: number of tokens in the prompt (input)
        - completion_tokens: number of tokens in the model's final answer
        - total_tokens: sum of prompt and completion tokens
        - reasoning_tokens: number of tokens used for chain-of-thought (if present)
      If usage data is not available, returns an empty dict.
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    metrics = {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
    }
    # Reasoning tokens detail if available
    details = getattr(usage, "completion_tokens_details", None)
    if details and hasattr(details, "reasoning_tokens"):
        metrics["reasoning_tokens"] = getattr(details, "reasoning_tokens", None)
    return metrics 