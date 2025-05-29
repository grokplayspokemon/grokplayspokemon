"""
Model configuration utilities for GrokAgent.

This module provides helper functions to read and interpret environment variables
that configure the xAI API calls (model selection, temperature, top_p).
By centralizing these in one place, you can override behavior without code changes.
"""
import os

def get_model_name(default="grok-3-mini"):
    """
    Return the model name to use for xAI calls.

    Reads the `XAI_MODEL` environment variable, falling back to `default`.
    Example model names:
      - "grok-3-mini"
      - "grok-3"
      - "grok-3-mini-fast"

    Returns a string.
    """
    return os.getenv("XAI_MODEL", default)

def get_temperature(default=0.0):
    """
    Return the temperature to use for xAI calls.

    Temperature controls randomness in the model's output:
      - 0.0 = deterministic
      - 0.7 = more varied responses

    Reads the `XAI_TEMPERATURE` environment variable, falling back to `default`.
    Returns a float.
    """
    try:
        return float(os.getenv("XAI_TEMPERATURE", default))
    except (ValueError, TypeError):
        return default

def get_top_p(default=1.0):
    """
    Return the top_p value to use for xAI calls.

    top_p (nucleus sampling) controls output diversity by limiting
    the cumulative probability mass considered at each step.
    Reads the `XAI_TOP_P` environment variable, falling back to `default`.
    Returns a float.
    """
    try:
        return float(os.getenv("XAI_TOP_P", default))
    except (ValueError, TypeError):
        return default 