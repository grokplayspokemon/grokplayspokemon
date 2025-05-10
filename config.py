# config.py
import logging
import os

# --- Paths ---
# Assumes the script is run from the project root. Adjust if needed.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROM_FILENAME = "pokemon.gb"
ROM_PATH = os.path.join(PROJECT_ROOT, ROM_FILENAME)
INITIAL_SAVE_STATE = "/puffertank/llm_plays_pokemon/DATAPlaysPokemon/saves/paused_save_20250503_003511.state"
SAVE_STATE_DIR = os.path.join(PROJECT_ROOT, "saves")
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")

# --- Web Server ---
WEB_HOST = "0.0.0.0"
WEB_PORT = 3000

# --- Emulator ---
EMULATOR_HEADLESS = True # Run PyBoy without a window
EMULATOR_SOUND = False
SCREENSHOT_UPSCALE = 2 # Factor to upscale screenshots for the LLM

# --- LLM ---
# Required Environment Variables:
# - XAI_API_KEY (for Grok)
# - OPENAI_API_KEY (for OpenAI)
# - ANTHROPIC_API_KEY (for Anthropic)

LLM_PROVIDER = "grok" # Options: "grok", "openai", "anthropic"
LLM_TEMPERATURE = 0.85
LLM_MAX_TOKENS = 15000

# Provider-specific models
LLM_MODEL_ANTHROPIC = "claude-3-5-sonnet-20240620" # Or "claude-3-opus-20240229", "claude-3-sonnet-20240229", "claude-3-haiku-20240307"
LLM_MODEL_OPENAI = "gpt-4o-mini" # Or "gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo"
LLM_MODEL_GROK = "grok-3-mini-beta"

# --- Agent & Game Settings ---
MAX_HISTORY = 10 # Max messages before summarization
USE_OVERLAY = False # Overlays the screenshot w/ the visible screen tiles
USE_COLLISION_MAP = True # ASCII overlay provided to LLM
USE_SCREENSHOTS = False # Screenshots sent to LLM
USE_NAVIGATOR = True # Deprecated/Unused?
# Delay in seconds between each agent step for viewers to see updates
STEP_DELAY = 0.0

# --- Logging ---
LOG_LEVEL = logging.INFO # e.g., logging.DEBUG, logging.INFO, logging.WARNING

# --- Helper function to get the correct model based on provider ---
def get_model_name():
    if LLM_PROVIDER == "anthropic":
        return LLM_MODEL_ANTHROPIC
    elif LLM_PROVIDER == "openai":
        return LLM_MODEL_OPENAI
    elif LLM_PROVIDER == "grok":
        return LLM_MODEL_GROK
    else:
        # Default or raise error if needed
        return LLM_MODEL_GROK

