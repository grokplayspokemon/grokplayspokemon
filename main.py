import argparse
import logging
import os
import asyncio
import uvicorn
from web.app import app
from agent.simple_agent import SimpleAgent
from contextlib import asynccontextmanager
from datetime import datetime
import config
import glob
from pathlib import Path
from config import SAVE_STATE_DIR

# Use paths from config
logs_dir = config.LOG_DIR
os.makedirs(logs_dir, exist_ok=True)

# Create a unique log directory for this run
current_time = datetime.now().strftime("%Y%m%d_%H%M%S")
run_log_dir = os.path.join(logs_dir, f"run_{current_time}")
os.makedirs(run_log_dir, exist_ok=True)
os.makedirs(os.path.join(run_log_dir, "frames"), exist_ok=True)
run_save_state_dir = os.path.join(config.SAVE_STATE_DIR, f"run_{current_time}") # Use config save dir
os.makedirs(run_save_state_dir, exist_ok=True)

# Set up logging using config level
logging.basicConfig(
    level=config.LOG_LEVEL,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(run_log_dir, "game.log"))
    ],
)

logger = logging.getLogger(__name__)

# Create a separate logger for Grok's messages
grok_logger = logging.getLogger("grok")
grok_logger.setLevel(config.LOG_LEVEL) # Use config level
grok_handler = logging.FileHandler(os.path.join(run_log_dir, "grok_messages.log"))
grok_handler.setFormatter(logging.Formatter("%(asctime)s - %(message)s"))
grok_logger.addHandler(grok_handler)

@asynccontextmanager
async def lifespan(app):
    # Store log directory and grok logger in app state
    app.state.run_log_dir = run_log_dir
    app.state.grok_logger = grok_logger
    app.state.run_save_state_dir = run_save_state_dir # Where saved pyboy states go
    app.state.is_paused = False  # Initialize pause state
    
    # Startup: create agent but don't start it yet
    cfg = app.state.cfg # Get merged config from app state
    agent = SimpleAgent(
        cfg=cfg, # Pass the whole config object
        app=app, # Pass the app instance
    )
    app.state.agent = agent
    app.state.agent_task = None

    # Load autosave if present, otherwise fallback to initial save state
    try:
        autosaves = sorted(Path(SAVE_STATE_DIR).glob('autosave_*.state'), key=lambda p: p.stat().st_mtime)
        if autosaves:
            latest_auto = autosaves[-1]
            agent.emulator.load_state(str(latest_auto))
            logger.info(f"Loaded autosave state from {latest_auto}")
        elif cfg.initial_save_state:
            agent.emulator.load_state(cfg.initial_save_state)
            logger.info(f"Loaded initial save state from {cfg.initial_save_state}")
    except Exception as e:
        logger.error(f"Failed to load save state: {e}")
        logger.warning("Continuing without loading save state.")
    
    yield
    # Shutdown: cleanup
    if hasattr(app.state, 'agent_task') and app.state.agent_task:
        app.state.agent_task.cancel()
        try:
            await app.state.agent_task
        except asyncio.CancelledError:
            pass
    if hasattr(app.state, 'agent'):
        app.state.agent.stop()

app.router.lifespan_context = lifespan

def main():
    parser = argparse.ArgumentParser(description="LLM Plays Pokemon - Web Version",
                                     formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    # Make most args optional overrides of config.py
    parser.add_argument(
        "--rom", 
        type=str, 
        default=config.ROM_FILENAME, 
        help=f"Filename of the Pokemon ROM (default: {config.ROM_FILENAME})"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=config.WEB_PORT,
        help=f"Port to run the web server on (default: {config.WEB_PORT})"
    )
    parser.add_argument(
        "--max-history", 
        type=int, 
        default=config.MAX_HISTORY, 
        help=f"Maximum messages before summarization (default: {config.MAX_HISTORY})"
    )
    parser.add_argument(
        "--save-state",
        type=str,
        default=config.INITIAL_SAVE_STATE,
        help=f"Path to an initial save state file to load (default: {config.INITIAL_SAVE_STATE})"
    )
    # parser.add_argument(
    #     "--overlay",
    #     action=argparse.BooleanOptionalAction,
    #     default=config.USE_OVERLAY,
    #     help=f"Enable tile overlay on screenshots sent to LLM (default: {config.USE_OVERLAY})"
    # )
    parser.add_argument(
        "--provider",
        type=str,
        choices=["anthropic", "openai", "grok"],
        default=config.LLM_PROVIDER,
        help=f"LLM provider to use (default: {config.LLM_PROVIDER})"
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None, # Default determined by provider later
        help="Override LLM model name (defaults based on provider in config.py)"
    )
    parser.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=config.EMULATOR_HEADLESS,
        help=f"Run emulator without display (default: {config.EMULATOR_HEADLESS})"
    )
    parser.add_argument(
        "--sound",
        action=argparse.BooleanOptionalAction,
        default=config.EMULATOR_SOUND,
        help=f"Enable emulator sound (default: {config.EMULATOR_SOUND})"
    )
    parser.add_argument(
        "--steps",
        type=int,
        default=100000,
        help="Number of agent steps to run (default: 100000)"
    )
    parser.add_argument(
        "--xai-api-key",
        type=str,
        default=None,
        help="XAI/Grok API key (can also set via XAI_API_KEY env var)"
    )
    parser.add_argument(
        "--openai-api-key",
        type=str,
        default=None,
        help="OpenAI API key (can also set via OPENAI_API_KEY env var)"
    )
    parser.add_argument(
        "--anthropic-api-key",
        type=str,
        default=None,
        help="Anthropic API key (can also set via ANTHROPIC_API_KEY env var)"
    )

    parser.add_argument(
        "--use-collision-map",
        action=argparse.BooleanOptionalAction,
        default=config.USE_COLLISION_MAP,
        help=f"Enable collision map (default: {config.USE_COLLISION_MAP})"
    )
    
    parser.add_argument(
        "--use-screenshots",
        action=argparse.BooleanOptionalAction,
        default=config.USE_SCREENSHOTS,
        help=f"Enable screenshots (default: {config.USE_SCREENSHOTS})"
    )
    
    parser.add_argument(
        "--use-navigator",
        action=argparse.BooleanOptionalAction,
        default=config.USE_NAVIGATOR,
        help=f"Enable navigator (default: {config.USE_NAVIGATOR})"
    )
    
    
    
    
    args = parser.parse_args()

    # Set API keys from CLI if provided
    if args.xai_api_key:
        os.environ["XAI_API_KEY"] = args.xai_api_key
    if args.openai_api_key:
        os.environ["OPENAI_API_KEY"] = args.openai_api_key
    if args.anthropic_api_key:
        os.environ["ANTHROPIC_API_KEY"] = args.anthropic_api_key

    # --- Merge argparse args with config defaults --- 
    # Create a simple namespace or dict to hold the final config
    class MergedConfig:
        pass
    cfg = MergedConfig()
    
    # Paths
    cfg.rom_filename = args.rom
    cfg.initial_save_state_arg = args.save_state # Store cli arg separately for path resolution
    cfg.save_state_dir = config.SAVE_STATE_DIR
    cfg.log_dir = config.LOG_DIR
    
    # Web Server
    cfg.web_host = config.WEB_HOST
    cfg.web_port = args.port
    
    # Emulator
    cfg.emulator_headless = args.headless
    cfg.emulator_sound = args.sound
    cfg.screenshot_upscale = config.SCREENSHOT_UPSCALE
    
    # LLM
    cfg.llm_provider = args.provider
    cfg.llm_temperature = config.LLM_TEMPERATURE
    cfg.llm_max_tokens = config.LLM_MAX_TOKENS
    # Determine model based on provider and CLI override
    if args.model:
        cfg.llm_model = args.model
    else:
        if cfg.llm_provider == "anthropic":
            cfg.llm_model = config.LLM_MODEL_ANTHROPIC
        elif cfg.llm_provider == "openai":
            cfg.llm_model = config.LLM_MODEL_OPENAI
        elif cfg.llm_provider == "grok":
            cfg.llm_model = config.LLM_MODEL_GROK
        else:
            cfg.llm_model = config.LLM_MODEL_GROK # Fallback default

    # Agent & Game Settings
    cfg.max_history = args.max_history
    # cfg.use_overlay = args.overlay
    cfg.num_steps = args.steps # Add steps to config
    cfg.use_collision_map = args.use_collision_map
    cfg.use_screenshots = args.use_screenshots
    cfg.use_navigator = args.use_navigator
    # Delay in seconds between each agent step for viewers (from config)
    cfg.step_delay = config.STEP_DELAY
    
    # Logging
    cfg.log_level = config.LOG_LEVEL

    # Resolve ROM path
    if not os.path.isabs(cfg.rom_filename):
        cfg.rom_path = os.path.join(config.PROJECT_ROOT, cfg.rom_filename)
    else:
        cfg.rom_path = cfg.rom_filename
    if not os.path.exists(cfg.rom_path):
        logger.error(f"ROM file not found: {cfg.rom_path}")
        print(f"\nYou need to provide a valid Pokemon ROM file ({cfg.rom_filename}).")
        print("Place it in the project root or specify its full path with --rom.")
        return

    # Resolve initial save state path
    cfg.initial_save_state = None
    if cfg.initial_save_state_arg:
        if not os.path.isabs(cfg.initial_save_state_arg):
            cfg.initial_save_state = os.path.join(config.PROJECT_ROOT, cfg.initial_save_state_arg)
        else:
            cfg.initial_save_state = cfg.initial_save_state_arg
        if not os.path.exists(cfg.initial_save_state):
            logger.error(f"Initial save state file not found: {cfg.initial_save_state}")
            # Decide: exit or continue? For now, clear the invalid path and continue.
            logger.warning("Ignoring invalid --save-state path.")
            cfg.initial_save_state = None
            
    # Store merged config in app state for lifespan access
    app.state.cfg = cfg
    
    # Run the FastAPI app with uvicorn using config host/port
    uvicorn.run(app, host=cfg.web_host, port=cfg.web_port)

if __name__ == "__main__":
    main()