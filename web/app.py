from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, status, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
import asyncio
import logging
import json
import os # For API keys from environment

from web.agent_runner import run_agent
from agent.simple_agent import SimpleAgent
from agent.memory_reader import PokemonRedReader

# Environment components
from environment.play import get_default_config
from environment.wrappers.env_wrapper import EnvWrapper
from environment.environment_helpers.navigator import InteractiveNavigator
from environment.environment_helpers.quest_manager import QuestManager

# For loading OmegaConf or similar if get_default_config returns it
from omegaconf import OmegaConf 

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

static_dir = Path(__file__).resolve().parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")
templates = Jinja2Templates(directory=Path(__file__).resolve().parent / "templates")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Application State Setup ---
# This would typically be done when the app starts, e.g. in a startup event or main block.
# For simplicity, we'll initialize parts of app.state here or assume they are set.
# In a real app, 'args' might come from command-line parsing.

# Project root to correctly locate config.yaml and other resources
project_root = Path(__file__).resolve().parent.parent

# Load the main configuration once
# get_default_config expects rom_path, but for loading general config, we might not need it immediately
# or it's defined in config.yaml itself.
# Let's assume config.yaml has env.gb_path for the rom.
# The rom_path for get_default_config can be a default or placeholder if just loading.
# However, play.py's get_default_config requires rom_path. Let's provide a default.

# A more robust way to get project root for config loading within web.app context
# Path(__file__).parent.parent gives grok_plays_pokemon directory
config_file_path = project_root / "config.yaml"

# Load base configuration using OmegaConf directly or via get_default_config
# For simplicity, we'll assume get_default_config can load the whole thing
# or we load it and pass parts. play.py's get_default_config is tied to env section.

# Let's load the full config here for app-wide access
# We use a placeholder rom_path if get_default_config absolutely needs it for initial load,
# but true path will come from the config itself for EnvWrapper.
# A better get_default_config would allow loading without specific overrides first.

# Simplified: Load config directly for agent params, and use get_default_config for env_wrapper
# This avoids making get_default_config too complex for this step.
# We'll assume config.yaml is at project_root / "config.yaml"

# Load the OmegaConf object directly for the whole config
# Ensure config.yaml path is correct

@app.on_event("startup")
async def startup_event():
    app.state.agent_task = None
    app.state.is_paused = False
    app.state.agent_instance = None # Store the agent instance
    app.state.env_wrapper_instance = None # Store env_wrapper
    app.state.reader_instance = None
    app.state.navigator_instance = None
    app.state.quest_manager_instance = None

    # Prepare directories for logging
    app.state.run_log_dir = project_root / "runs" / "current_run" # Example
    os.makedirs(app.state.run_log_dir / "frames", exist_ok=True)
    app.state.claude_logger = logging.getLogger("claude_messages") # Example
    # Configure claude_logger if needed (e.g., file handler)

    # Load configuration (this should ideally be more robust, e.g. using Pydantic model for config)
    try:
        # Assuming config.yaml is at the root of the grok_plays_pokemon project
        # The Path(__file__).parent.parent should point to grok_plays_pokemon/
        cfg_path = project_root / "config.yaml"
        if not cfg_path.exists():
            logger.error(f"CRITICAL: config.yaml not found at {cfg_path}. Please ensure it exists.")
            # App might not function correctly without it.
            app.state.config = OmegaConf.create({}) # Empty config to prevent crashes, but issues will occur
        else:
            app.state.config = OmegaConf.load(cfg_path)
            logger.info(f"Loaded configuration from {cfg_path}")
    except Exception as e:
        logger.error(f"Error loading config.yaml: {e}. Using empty config.")
        app.state.config = OmegaConf.create({}) 

    # Placeholder for args that might come from CLI or other sources
    # These should ideally also be in config.yaml if not CLI-driven
    app.state.args = OmegaConf.create({
        "rom_path": app.state.config.get("env_config", {}).get("gb_path", str(project_root / "pokered.gb")),
        "max_history": app.state.config.get("agent", {}).get("max_history", 60), # Example, add to config if needed
        "steps": app.state.config.get("run_settings", {}).get("num_steps", 1000), # Example, add to config
        # provider and model_name will be taken from config.agent directly
    })

# WebSocket connection manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self.active_connections.append(websocket)
        logger.info(f"New WebSocket connection. Total connections: {len(self.active_connections)}")

    async def disconnect(self, websocket: WebSocket):
        async with self._lock:
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)
        logger.info(f"WebSocket disconnected. Remaining connections: {len(self.active_connections)}")

    async def broadcast(self, message: str):
        if not self.active_connections:
            logger.warning("No active connections to broadcast to")
            return
            
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except WebSocketDisconnect:
                disconnected.append(connection)
            except Exception as e:
                logger.error(f"Error sending message: {e}")
                disconnected.append(connection)
        
        # Clean up disconnected clients
        for conn in disconnected:
            await self.disconnect(conn)

manager = ConnectionManager()

# Routes
@app.get("/", response_class=HTMLResponse)
async def get_home(request: Request):
    # Use provider and model from the loaded config for the template
    provider = request.app.state.config.get("agent", {}).get("llm_provider", "grok")
    model_name = request.app.state.config.get("agent", {}).get("model_name", "grok-1")
    context = {
        "request": request,
        "provider": provider,
        "model_name": model_name,
    }
    return templates.TemplateResponse(
        "index.html",
        context
    )

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            try:
                data = await websocket.receive_text()
                # Echo back the received data for testing
                await websocket.send_text(f"Server received: {data}")
            except WebSocketDisconnect:
                await manager.disconnect(websocket)
                break
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                break
    finally:
        await manager.disconnect(websocket)

# Updated function to send game state updates, including structured game state
async def send_game_updates(frame_data: bytes = None, game_state_update: dict = None, claude_message: str = None):
    """Sends frame, structured game state, and LLM message to connected clients."""
    try:
        message = {
            "type": "update",
        }
        if frame_data:
            message["frame"] = frame_data.hex()  
        
        if game_state_update: 
            message["game_state"] = game_state_update 
        
        if claude_message: # Renamed from claude_message to generic llm_message if provider changes
            message["message"] = claude_message 
        
        if "frame" in message or "game_state" in message or "message" in message:
            await manager.broadcast(json.dumps(message))
    except Exception as e:
        logger.error(f"Error sending game updates: {e}", exc_info=True)

@app.post("/start")
async def start_agent_endpoint(): # Renamed to avoid conflict with run_agent import
    if app.state.agent_task is not None and not app.state.agent_task.done():
        return {"status": "error", "message": "Agent is already running"}
    
    try:
        app.state.is_paused = False
        config = app.state.config # Get the loaded OmegaConf object

        # --- Initialize Environment Components ---
        env_settings = config.get("env", {}) # Get the 'env' section from config.yaml
        rom_path_from_config = env_settings.get("gb_path", app.state.args.rom_path) # Use env.gb_path if available
        if not Path(rom_path_from_config).is_file():
             # Try relative to project root if absolute fails or not specified fully
             test_path = project_root / rom_path_from_config
             if test_path.is_file():
                 rom_path_from_config = str(test_path)
             else:
                 logger.error(f"ROM file not found at specified path: {rom_path_from_config} or {test_path}. Please check config.yaml: env.gb_path")
                 return {"status": "error", "message": f"ROM not found: {rom_path_from_config}"}

        initial_state_path_from_config = env_settings.get("override_init_state", str(project_root / "initial_states" / "init.state"))
        if not Path(initial_state_path_from_config).is_file() and not env_settings.get("override_init_state"):
            logger.warning(f"Default initial state {initial_state_path_from_config} not found. Game will start from beginning of ROM if override_init_state is not set or file missing.")
        
        required_completions_path = str(project_root / "environment" / "environment_helpers" / "required_completions.json")

        # Use get_default_config to build the specific EnvWrapper config, applying overrides from config.yaml
        env_wrapper_config = get_default_config(
            rom_path=rom_path_from_config,
            initial_state_path=initial_state_path_from_config,
            # Pass other values from config.env if they are arguments to get_default_config
            # or rely on get_default_config to read them from its loaded full_config.yaml
            # For example:
            headless=env_settings.get("headless", True), 
            emulator_delay=env_settings.get("emulator_delay", 1),
            disable_ai_actions=False, # Agent must control
            # ... and other relevant env settings from your config.yaml that get_default_config uses
            use_global_map=env_settings.get("use_global_map", False),
            infinite_money=env_settings.get("infinite_money",False), 
            infinite_health=env_settings.get("infinite_health", False),
            disable_wild_encounters=env_settings.get("disable_wild_encounters", False),
            # ... etc for all relevant env params in get_default_config signature
        )
        # If get_default_config already loaded the full config.yaml, env_wrapper_config IS that config object.
        # If it MERGES args with a loaded config, then it's the result of that merge.

        if app.state.env_wrapper_instance is None: # Initialize only if not already running/exists
            app.state.env_wrapper_instance = EnvWrapper(env_wrapper_config) 
            app.state.reader_instance = PokemonRedReader(app.state.env_wrapper_instance.pyboy.mb)
            app.state.navigator_instance = InteractiveNavigator(app.state.env_wrapper_instance, use_global_map=env_wrapper_config.env.use_global_map)
            app.state.quest_manager_instance = QuestManager(app.state.env_wrapper_instance, app.state.navigator_instance, required_completions_path)
            logger.info("Environment components initialized for new agent run.")
        else:
            logger.info("Using existing environment components for agent run.")
            # TODO: Consider if env_wrapper needs reset or re-config if settings changed

        # --- Initialize SimpleAgent ---
        agent_settings = config.get("agent", {})
        llm_provider = agent_settings.get("llm_provider", "grok")
        model_name = agent_settings.get("model_name", "grok-1")
        max_tokens = agent_settings.get("max_tokens", 2048)
        temperature = agent_settings.get("temperature", 0.7)
        # API key handling: prefer environment variable, then config.yaml (not recommended for keys in file)
        grok_api_key = os.environ.get("GROK_API_KEY") or agent_settings.get("api_key")

        # Create or reuse SimpleAgent instance
        # To ensure SimpleAgent has the correct env components, it should be created here if not existing
        # or if env components have been re-initialized.
        # For simplicity, let's re-create it for each /start if it's not already running.
        # A more robust solution might involve agent pooling or careful state management.
        
        app.state.agent_instance = SimpleAgent(
            max_history=app.state.args.max_history,
            app=app, # Pass FastAPI app instance if needed by agent
            # LLM Config
            provider=llm_provider,
            model_name=model_name,
            max_tokens_config=max_tokens, # Pass specifically named config
            temperature_config=temperature, # Pass specifically named config
            grok_api_key=grok_api_key, 
            # Environment Components
            env_wrapper=app.state.env_wrapper_instance,
            reader=app.state.reader_instance,
            quest_manager=app.state.quest_manager_instance,
            navigator=app.state.navigator_instance
        )
        logger.info(f"SimpleAgent initialized with provider: {llm_provider}, model: {model_name}")
        
        app.state.agent_task = asyncio.create_task(
            run_agent(
                simple_agent=app.state.agent_instance, # Use the newly created/configured agent
                app_state=app.state, # Pass the whole app.state for pause flag etc.
                num_steps=app.state.args.steps,
                run_log_dir=app.state.run_log_dir,
                send_game_updates=send_game_updates,
                claude_logger=app.state.claude_logger 
            )
        )
        return {"status": "success", "message": "Agent started successfully"}
    except Exception as e:
        logger.error(f"Error starting agent: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}

@app.post("/pause")
async def pause_agent():
    if not hasattr(app.state, 'agent_task') or app.state.agent_task is None:
        return {"status": "error", "message": "No agent is running"}
    
    try:
        # Toggle pause state
        app.state.is_paused = not getattr(app.state, 'is_paused', False)
        return {"status": "success", "message": "Agent pause state toggled"}
    except Exception as e:
        logger.error(f"Error toggling pause state: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/stop")
async def stop_agent():
    if not hasattr(app.state, 'agent_task') or app.state.agent_task is None:
        return {"status": "error", "message": "No agent is running"}
    
    try:
        # Cancel the running task
        app.state.agent_task.cancel()
        try:
            await app.state.agent_task
        except asyncio.CancelledError:
            pass
        
        # Reset state
        app.state.agent_task = None
        app.state.is_paused = False
        
        # Save logs if needed
        logger.info("Agent stopped, logs saved")
        
        return {"status": "success", "message": "Agent stopped successfully"}
    except Exception as e:
        logger.error(f"Error stopping agent: {e}")
        return {"status": "error", "message": str(e)}

@app.get("/status")
async def get_agent_status():
    is_running = app.state.agent_task is not None and not app.state.agent_task.done()
    is_paused = getattr(app.state, 'is_paused', False)
    
    if not is_running:
        return {"status": "stopped"}
    elif is_paused:
        return {"status": "paused"}
    else:
        return {"status": "running"}

@app.post("/upload-save-state")
async def upload_save_state(file: UploadFile = File(...)):
    try:
        # Create saves directory if it doesn't exist
        saves_dir = Path("saves")
        saves_dir.mkdir(exist_ok=True)
        
        # Validate file extension
        if not file.filename.endswith('.state'):
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"status": "error", "message": "Invalid file type. Must be a PyBoy .state file."}
            )
        
        # Save the uploaded file
        save_path = saves_dir / file.filename
        with save_path.open("wb") as f:
            contents = await file.read()
            f.write(contents)
        
        # Load the save state into the emulator if agent exists
        if hasattr(app.state, 'agent'):
            try:
                app.state.agent.emulator.load_state(str(save_path))
                logger.info(f"Loaded save state from {save_path}")
                return JSONResponse({"status": "success", "message": "Save state loaded successfully"})
            except Exception as e:
                logger.error(f"Failed to load save state: {e}")
                return JSONResponse(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    content={"status": "error", "message": f"Failed to load save state: {str(e)}"}
                )
        else:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"status": "error", "message": "Agent not initialized"}
            )
            
    except Exception as e:
        logger.error(f"Error handling save state upload: {e}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"status": "error", "message": str(e)}
        ) 