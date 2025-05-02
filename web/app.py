from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, status, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
import asyncio
import logging
import json
from web.agent_runner import run_agent
from agent.simple_agent import SimpleAgent

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
app.mount("/static", StaticFiles(directory="web/static"), name="static")

# Setup templates
templates = Jinja2Templates(directory="web/templates")

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
    return templates.TemplateResponse(
        "index.html",
        {"request": request}
    )

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            try:
                raw = await websocket.receive_text()
                # Handle incoming JSON messages (e.g., input events)
                try:
                    msg = json.loads(raw)
                    mtype = msg.get("type")
                    if mtype == "input":
                        button = msg.get("button")
                        if hasattr(app.state, 'agent'):
                            app.state.agent.emulator.press_buttons([button], True)
                            # Broadcast updated frame so Dev Mode shows the result immediately
                            frame = app.state.agent.get_frame()
                            await send_game_updates(frame, f"Dev pressed {button}")
                            logger.info(f"Dev input broadcasted: {button}")
                            # Log and ack the dev input
                            logger.info(f"Dev input received: {button}")
                            await websocket.send_text(json.dumps({"type": "ack", "button": button}))
                    # ignore 'ping' and other types
                except json.JSONDecodeError:
                    logger.warning(f"Received non-JSON message: {raw}")
            except WebSocketDisconnect:
                await manager.disconnect(websocket)
                break
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                break
    finally:
        await manager.disconnect(websocket)

# Function to send game state updates
async def send_game_updates(frame_data: bytes, grok_message: str):
    try:
        # Fetch emulator memory state for debugging
        memory_info = None
        if hasattr(app.state, 'agent') and hasattr(app.state.agent, 'emulator'):
            try:
                memory_info = app.state.agent.emulator.get_state_from_memory()
            except Exception as e:
                logger.error(f"Error getting memory info: {e}")
        message = {
            "type": "update",
            "frame": frame_data.hex(),  # Convert bytes to hex string
            "message": grok_message,
            "memory": memory_info
        }
        await manager.broadcast(json.dumps(message))
    except Exception as e:
        logger.error(f"Error sending game updates: {e}")

@app.post("/start")
async def start_agent():
    if app.state.agent_task is not None and not app.state.agent_task.done():
        return {"status": "error", "message": "Agent is already running"}
    
    try:
        # Reset the pause flag when starting
        app.state.is_paused = False
        
        # Agent should have been created during lifespan startup
        if not hasattr(app.state, 'agent'):
            # If we don't have an agent yet, create one (This block should ideally not be hit)
            logger.error("Agent not found in app state during start request. Re-creating (check lifespan). ")
            # We need cfg here, but it should already be in app.state from lifespan
            if not hasattr(app.state, 'cfg'):
                 return {"status": "error", "message": "Config not found in app state."}
            app.state.agent = SimpleAgent(
                cfg=app.state.cfg, # Use config object
                app=app
            )
        
        # Ensure cfg is available before creating the task
        if not hasattr(app.state, 'cfg'):
            logger.error("Config not found in app state before creating agent task.")
            return {"status": "error", "message": "Config not found in app state."}
            
        app.state.agent_task = asyncio.create_task(
            run_agent(
                agent=app.state.agent,
                num_steps=app.state.cfg.num_steps, # Use cfg object instead
                run_log_dir=app.state.run_log_dir,
                send_game_updates=send_game_updates,
                grok_logger=app.state.grok_logger
            )
        )
        return {"status": "success", "message": "Agent started successfully"}
    except Exception as e:
        logger.error(f"Error starting agent: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/pause")
async def pause_agent():
    if not hasattr(app.state, 'agent_task') or app.state.agent_task is None:
        return {"status": "error", "message": "No agent is running"}
    
    try:
        # Toggle pause state
        was_paused = getattr(app.state, 'is_paused', False)
        app.state.is_paused = not was_paused
        
        # Save state if pausing
        if app.state.is_paused:
            logger.info("Agent is being paused. Attempting to save state...")
            if hasattr(app.state, 'agent') and hasattr(app.state.agent, 'emulator'):
                try:
                    save_path = app.state.agent.emulator.save_state(filename_prefix="paused_save")
                    if save_path:
                        logger.info(f"State saved successfully to {save_path} on pause.")
                        return {"status": "success", "message": "Agent paused and state saved.", "save_path": save_path}
                    else:
                        logger.warning("save_state returned None, state not saved.")
                        return {"status": "success", "message": "Agent paused, but state save failed."}
                except Exception as e:
                     logger.error(f"Error saving state during pause: {e}", exc_info=True)
                     return {"status": "error", "message": f"Agent paused, but failed to save state: {e}"}
            else:
                logger.warning("Could not find agent or emulator to save state.")
                return {"status": "success", "message": "Agent paused, but could not access emulator to save state."}
        else:
            # Resuming
            logger.info("Agent is being resumed.")
            return {"status": "success", "message": "Agent resumed."}

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