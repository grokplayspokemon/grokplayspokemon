import logging
logger = logging.getLogger(__name__)

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, status, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
import uuid
import asyncio
import logging
import json
import time
import io
from web.agent_runner import run_agent
from agent.simple_agent import SimpleAgent
import os

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

# Enable dev control mode via environment variable DEV_CONTROL
app.state.dev_control = os.getenv("DEV_CONTROL", "false").lower() in ("1","true")

# Routes
@app.get("/", response_class=HTMLResponse)
async def get_home(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {"request": request}
    )

async def send_game_updates(frame_data: bytes, grok_message: str):
    """Modified function to ensure UI elements are properly displayed."""
    import time
    import json
    
    # Create logger
    logger = logging.getLogger(__name__)
    
    # Generate a timestamp for tracking
    timestamp = int(time.time() * 1000)
    
    # Check for active connections
    if not manager.active_connections:
        logger.warning("No active WebSocket connections for frame update")
        return
        
    try:
        # Build the basic update message structure
        message = {
            "type": "update",
            "timestamp": timestamp
        }
        
        # Add frame data if it exists and is valid
        if frame_data and isinstance(frame_data, bytes) and len(frame_data) > 0:
            try:
                message["frame"] = frame_data.hex()
                logger.debug(f"Frame data valid: {len(frame_data)} bytes")
            except Exception as e:
                logger.error(f"Error converting frame data: {e}")
        
        # CRITICAL: Always add the message content for Grok's Thoughts
        message["message"] = grok_message
        logger.info(f"Adding message to update: {grok_message[:30]}...")
        
        # CRITICAL: Always get and include collision map data
        try:
            if hasattr(app.state, 'agent') and hasattr(app.state.agent, 'emulator'):
                # Retrieve raw collision data; fallback if missing
                raw_map = app.state.agent.emulator.get_collision_map()
                if not isinstance(raw_map, dict) or not raw_map.get('collision_map'):
                    # Create placeholder 9x10 grid of unwalkable cells with player at center
                    raw_map = {
                        'collision_map': [[{'x':0,'y':0,'walkable':False,'entity':None,'entity_id':None} for _ in range(10)] for _ in range(9)],
                        'player_position': {'x': -1, 'y': -1, 'direction': '?'},
                        'grid_position': {'row': 4, 'col': 4},
                        'recent_directions': [],
                        'warps': []
                    }
                message["collision_map_raw"] = raw_map
                # Format ASCII map with visit counts and player as P
                try:
                    ascii_map = app.state.agent.emulator.format_collision_map_with_counts(raw_map)
                except Exception as e:
                    logger.error(f"Error formatting collision counts: {e}", exc_info=True)
                    ascii_map = None
                message["collision_map"] = ascii_map
                # Also include warps list if provided
                warps = []
                if isinstance(raw_map, dict):
                    warps = raw_map.get('warps', []) or []
                message["warps"] = warps
                # Fallback: also log ASCII collision map to game.log for resilience
                if ascii_map:
                    logger.info("[ASCII MAP]\n" + ascii_map)
            else:
                # No emulator available, send placeholders
                message["collision_map_raw"] = None
                message["collision_map"] = None
                message["warps"] = []
        except Exception as e:
            logger.error(f"Error getting collision map: {e}", exc_info=True)
            message["collision_map_raw"] = None
            message["collision_map"] = None
            message["warps"] = []
        
        # Convert to JSON and broadcast with retries
        try:
            message_json = json.dumps(message)
            retry_count = 0
            max_retries = 3
            
            while retry_count < max_retries:
                try:
                    await manager.broadcast(message_json)
                    logger.info(f"Update with timestamp {timestamp} sent to {len(manager.active_connections)} connections")
                    break
                except Exception as e:
                    retry_count += 1
                    if retry_count >= max_retries:
                        logger.error(f"Failed to send update after {max_retries} attempts: {e}")
                    else:
                        logger.warning(f"Retry {retry_count}/{max_retries} sending update: {e}")
                        await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"Error preparing message for broadcast: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Error in send_game_updates: {e}", exc_info=True)
        
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    
    # Start the agent automatically if it's not running
    if not hasattr(app.state, 'agent_task') or app.state.agent_task is None or app.state.agent_task.done():
        logger.info("WebSocket connected, starting agent automatically.")
        try:
            # This calls the existing start_agent logic
            await start_agent()
        except Exception as e:
            logger.error(f"Error starting agent automatically on WebSocket connection: {e}")

    # Send an initial connection confirmation with timestamp
    try:
        await websocket.send_text(json.dumps({
            "type": "connected",
            "message": "WebSocket connection established",
            "timestamp": int(time.time() * 1000)
        }))
        logger.info("Sent connection confirmation to new WebSocket client")
        
        # Send a refresh frame immediately to update the UI
        if hasattr(app.state, 'agent'):
            try:
                frame = app.state.agent.get_frame()
                location = app.state.agent.emulator.get_location() or "Unknown"
                await send_game_updates(frame, f"Connected to game in {location}")
            except Exception as e:
                logger.error(f"Error sending initial frame: {e}")
    except Exception as e:
        logger.error(f"Error sending connection confirmation: {e}")
    
    # Setup a heartbeat task to keep connection alive
    heartbeat_task = None
    try:
        # Start a heartbeat task
        async def heartbeat():
            try:
                while True:
                    await asyncio.sleep(15)  # Send heartbeat every 15 seconds
                    try:
                        await websocket.send_text(json.dumps({
                            "type": "heartbeat",
                            "timestamp": int(time.time() * 1000)
                        }))
                        logger.debug("Heartbeat sent")
                    except Exception as e:
                        logger.error(f"Heartbeat error: {e}")
                        return  # Exit the heartbeat task if we can't send
            except asyncio.CancelledError:
                # Clean termination of heartbeat
                pass
        
        # Start the heartbeat
        heartbeat_task = asyncio.create_task(heartbeat())
        
        # Main message processing loop
        while True:
            # Receive a message with timeout; send ping on timeout to keep connection alive
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
            except asyncio.TimeoutError:
                # Send ping to client to prevent idle timeout
                try:
                    await websocket.send_text(json.dumps({"type": "ping", "timestamp": int(time.time() * 1000)}))
                    logger.debug("Sent ping to client to keep connection alive")
                except Exception as e:
                    logger.warning(f"Ping failed, closing WebSocket: {e}")
                    break
                continue
            except WebSocketDisconnect:
                logger.info("WebSocket disconnected by client")
                break
            
            # Process the received message
            try:
                msg = json.loads(raw)
                mtype = msg.get("type")
                
                # Handle ping messages
                if mtype == "ping":
                    # Respond to ping from client
                    await websocket.send_text(json.dumps({"type": "pong", "timestamp": int(time.time() * 1000)}))
                
                # Handle input messages (dev control)
                elif mtype == "input" and hasattr(app.state, 'agent'):
                    button = msg.get("button")
                    if button:
                        app.state.agent.emulator.press_buttons([button], True)
                        
                        # Get updated frame and send immediately
                        frame = app.state.agent.get_frame()
                        if frame:
                            await send_game_updates(
                                frame, 
                                f"Dev pressed {button}"
                            )
                        
                        # Acknowledge input
                        await websocket.send_text(json.dumps({
                            "type": "ack",
                            "button": button,
                            "timestamp": int(time.time() * 1000)
                        }))
                
                # Handle refresh frame request
                elif mtype == "refresh_frame":
                    if hasattr(app.state, 'agent'):
                        frame = app.state.agent.get_frame()
                        location = app.state.agent.emulator.get_location() or "Unknown"
                        if frame:
                            await send_game_updates(frame, f"Frame refresh in {location}")
                            logger.info(f"Sent frame refresh for {location}")
            
            except json.JSONDecodeError:
                logger.warning(f"Received non-JSON message: {raw[:50]}...")
            
            except Exception as e:
                logger.error(f"WebSocket error: {e}", exc_info=True)
                # Try to continue if possible
                await asyncio.sleep(1.0)
    
    finally:
        # Clean up connection and heartbeat
        if heartbeat_task:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
        
        # Clean up connection
        await manager.disconnect(websocket)
        logger.info("WebSocket connection closed and cleaned up")

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
    return {"status": "error", "message": "Pause not supported"}

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
        
        # Auto-save state on stop
        if hasattr(app.state, 'agent') and hasattr(app.state.agent, 'emulator'):
            try:
                save_path = app.state.agent.emulator.save_state(filename_prefix="autosave")
                logger.info(f"State saved successfully to {save_path} on stop.")
                return {"status": "success", "message": "Agent stopped successfully", "save_path": save_path}
            except Exception as e:
                logger.error(f"Error saving state on stop: {e}")
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
                
                # Get and send the updated frame
                frame = app.state.agent.get_frame()
                await send_game_updates(frame, f"Loaded save state: {file.filename}")
                
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

@app.get("/logs")
async def get_logs():
    """Return the last 50 lines of game.log and grok_messages.log"""
    try:
        run_dir = Path(app.state.run_log_dir)
        game_path = run_dir / "game.log"
        grok_path = run_dir / "grok_messages.log"
        # Read last 50 lines of each log
        game_lines = game_path.read_text().splitlines()[-50:]
        grok_lines = grok_path.read_text().splitlines()[-50:]
        return JSONResponse({"game_log": game_lines, "grok_messages": grok_lines})
    except Exception as e:
        logger.error(f"Failed to read logs: {e}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": str(e)}
        )