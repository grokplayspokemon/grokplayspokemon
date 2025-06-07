# app.py
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, status, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
import asyncio
import logging
import json
import os
import subprocess
import signal
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

# Project root to correctly locate config.yaml and other resources
project_root = Path(__file__).resolve().parent.parent

@app.on_event("startup")
async def startup_event():
    """Initialize basic app state - no environment setup here"""
    app.state.play_process = None
    app.state.is_paused = False

    # Load configuration for UI display only
    try:
        cfg_path = project_root / "config.yaml"
        if cfg_path.exists():
            app.state.config = OmegaConf.load(cfg_path)
            logger.info(f"Loaded configuration from {cfg_path}")
        else:
            logger.error(f"CRITICAL: config.yaml not found at {cfg_path}")
            app.state.config = OmegaConf.create({})
    except Exception as e:
        logger.error(f"Error loading config.yaml: {e}")
        app.state.config = OmegaConf.create({})

# WebSocket connection manager for real-time updates
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
            return
            
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except (WebSocketDisconnect, Exception) as e:
                disconnected.append(connection)
        
        # Clean up disconnected clients
        for conn in disconnected:
            await self.disconnect(conn)

manager = ConnectionManager()

# Routes
@app.get("/", response_class=HTMLResponse)
async def get_home(request: Request):
    """Serve the main UI page"""
    agent_config = request.app.state.config.get("agent", {})
    provider = agent_config.get("llm_provider", "grok")
    model_name = agent_config.get("model_name", "grok-3-mini")
    grok_on = agent_config.get("grok_on", True)
    
    context = {
        "request": request,
        "provider": provider,
        "model_name": model_name,
        "grok_on": grok_on,
    }
    return templates.TemplateResponse("index.html", context)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time communication"""
    await manager.connect(websocket)
    try:
        while True:
            try:
                data = await websocket.receive_text()
                # Handle incoming WebSocket messages here
                await websocket.send_text(f"Server received: {data}")
            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                break
    finally:
        await manager.disconnect(websocket)

@app.get("/events")
async def stream_events(request: Request):
    """Server-Sent Events endpoint for real-time updates from play.py"""
    async def event_generator():
        try:
            while True:
                # In a real implementation, this would connect to play.py's output
                # For now, we'll send periodic heartbeat messages
                yield f"data: {json.dumps({'type': 'heartbeat', 'timestamp': str(asyncio.get_event_loop().time())})}\n\n"
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            pass

    return StreamingResponse(event_generator(), media_type="text/plain")

@app.get("/required_completions.json")
async def get_required_completions():
    """Serve quest definitions for the UI"""
    try:
        quest_file = project_root / "environment" / "environment_helpers" / "required_completions.json"
        with open(quest_file, 'r') as f:
            quests = json.load(f)
        return quests
    except Exception as e:
        logger.error(f"Error loading quest definitions: {e}")
        return []

@app.post("/start")
async def start_game():
    """Start the game via play.py with Grok enabled"""
    try:
        if app.state.play_process and app.state.play_process.poll() is None:
            return JSONResponse(
                status_code=400,
                content={"status": "error", "message": "Game is already running"}
            )

        # Start play.py with Grok enabled
        play_script = project_root / "environment" / "play.py"
        config_path = project_root / "config.yaml"
        
        # Ensure config has grok enabled
        config = OmegaConf.load(config_path)
        if not config.get("agent", {}).get("grok_on", False):
            config.agent.grok_on = True
            OmegaConf.save(config, config_path)
            logger.info("Enabled Grok in config.yaml")

        # Start play.py process
        app.state.play_process = subprocess.Popen([
            "python", str(play_script),
            "--config_path", str(config_path),
            "--interactive_mode", "false"  # Disable manual control
        ], cwd=str(project_root))
        
        logger.info(f"Started play.py process with PID {app.state.play_process.pid}")
        
        return JSONResponse(content={
            "status": "success", 
            "message": "Game started with Grok agent",
            "pid": app.state.play_process.pid
        })
        
    except Exception as e:
        logger.error(f"Error starting game: {e}")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": str(e)}
        )

@app.post("/pause")
async def pause_game():
    """Pause/resume the game - this would need to be implemented in play.py"""
    app.state.is_paused = not app.state.is_paused
    status = "paused" if app.state.is_paused else "resumed"
    
    # In a real implementation, this would send a signal to play.py
    return JSONResponse(content={"status": status})

@app.post("/stop")
async def stop_game():
    """Stop the game by terminating play.py process"""
    try:
        if app.state.play_process and app.state.play_process.poll() is None:
            app.state.play_process.terminate()
            app.state.play_process.wait(timeout=5)
            logger.info("Stopped play.py process")
            
        app.state.play_process = None
        
        return JSONResponse(content={"status": "stopped"})
        
    except subprocess.TimeoutExpired:
        # Force kill if it doesn't terminate gracefully
        app.state.play_process.kill()
        app.state.play_process = None
        logger.warning("Force killed play.py process")
        return JSONResponse(content={"status": "force_stopped"})
    except Exception as e:
        logger.error(f"Error stopping game: {e}")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": str(e)}
        )

@app.get("/status")
async def get_game_status():
    """Get current game status"""
    is_running = (app.state.play_process and 
                  app.state.play_process.poll() is None)
    
    return JSONResponse(content={
        "is_running": is_running,
        "is_paused": app.state.is_paused,
        "pid": app.state.play_process.pid if is_running else None
    })

@app.post("/upload-save-state")
async def upload_save_state(file: UploadFile = File(...)):
    """Upload a save state file"""
    try:
        # Save uploaded file to states directory
        states_dir = project_root / "environment" / "states"
        states_dir.mkdir(exist_ok=True)
        
        file_path = states_dir / file.filename
        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)
        
        logger.info(f"Uploaded save state: {file.filename}")
        
        return JSONResponse(content={
            "status": "success",
            "message": f"Save state '{file.filename}' uploaded successfully",
            "path": str(file_path)
        })
        
    except Exception as e:
        logger.error(f"Error uploading save state: {e}")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": str(e)}
        )

@app.get("/screenshot")
async def get_screenshot():
    """Get current game screenshot - in real implementation this would come from play.py"""
    # Placeholder response
    return JSONResponse(content={
        "status": "not_implemented",
        "message": "Screenshot endpoint not yet connected to play.py"
    })

# Cleanup on shutdown
@app.on_event("shutdown")
async def shutdown_event():
    """Clean shutdown - stop any running play.py process"""
    if app.state.play_process and app.state.play_process.poll() is None:
        try:
            app.state.play_process.terminate()
            app.state.play_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            app.state.play_process.kill()
        logger.info("Cleaned up play.py process on shutdown") 