# app.py
import logging
# Enable debug logging for troubleshooting
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

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
import base64
from web.button_queue import queue, process_next_button, set_orig_press

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
        # DEBUG: log broadcast message for troubleshooting
        logger.debug(f"Broadcasting message to {len(self.active_connections)} connections: {message[:100]}...")
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
            for connection in self.active_connections:
                if conn is connection:
                    self.active_connections.remove(conn)
                    break

manager = ConnectionManager()

# Enable dev control mode via environment variable DEV_CONTROL
app.state.dev_control = os.getenv("DEV_CONTROL", "false").lower() in ("1","true")

# Initialize pause flag so the agent does not run when dev control is active
app.state.is_paused = app.state.dev_control
if app.state.dev_control:
    logger.info("Dev control mode active: agent will start paused for manual control.")

# Routes
@app.get("/", response_class=HTMLResponse)
async def get_home(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {"request": request}
    )

async def send_game_updates(frame_data: bytes, grok_message: str):
    """Modified function to ensure UI elements are properly displayed with explicit sync."""
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
        # Determine if this is an Agent action message
        is_agent_action = grok_message.startswith("Agent:")
        message = {
            "type": "tool_result" if is_agent_action else "update",
            "timestamp": timestamp,
            "sync_id": timestamp  # Add sync_id for client-side synchronization
        }
        
        # Add frame data if it exists and is valid
        if frame_data and isinstance(frame_data, (bytes, bytearray)) and len(frame_data) > 0:
            try:
                # Encode full frame data as base64 for UI rendering
                message["frame"] = base64.b64encode(frame_data).decode('ascii')
                message["frame_format"] = "base64"
                logger.debug(f"Frame data valid: {len(frame_data)} bytes")
            except Exception as e:
                logger.error(f"Error processing frame (emulator screen) data: {e}")
        
        # Route the message based on type
        if is_agent_action:
            # Agent actions go to the Agent Actions pane
            message["content"] = [{"type": "text", "text": grok_message}]
        else:
            message["message"] = grok_message
            logger.info(f"Adding message to update: {grok_message[:500]}...")
        
        # Add Grok's latest thought if available
        if hasattr(app.state, 'agent') and hasattr(app.state.agent, 'get_latest_grok_thought'):
            grok_thought = app.state.agent.get_latest_grok_thought()
            message["grok_thought"] = grok_thought
            logger.debug(f"Added Grok's thought to update: {grok_thought[:500]}...")
        
        # Add party data if agent exists
        if hasattr(app.state, 'agent'):
            try:
                party_data = app.state.agent.get_party()
                if party_data:
                    message["party"] = party_data
                    logger.debug(f"Added party data: {len(party_data)} Pokémon")
            except Exception as e:
                logger.error(f"Error getting party data: {e}")
            # Add ASCII collision map under the game screen
            try:
                collision_data = app.state.agent.emulator.get_collision_map()
                collision_ascii = app.state.agent.emulator.format_collision_map_simple(collision_data)
                message["collision_map"] = collision_ascii
                logger.debug("Added collision_map to update")
            except Exception as e:
                logger.error(f"Error adding collision_map: {e}")
        
        # Add progress bar info for next goal
        try:
            import game_data.ram_map_leanke as ram_map
            progression = getattr(app.state.agent, 'game_progression', {}) or {}
            relevant_areas = progression.get('major_areas', {})
            next_goal = None
            for area_name in relevant_areas:
                if area_name.upper().startswith('ROUTE'):
                    next_goal = area_name
                    break
            percent = 0
            if next_goal:
                events = ram_map.monitor_route_events(app.state.agent.emulator.pyboy)
                key_prefix = next_goal.lower().replace(' ', '')
                route_events = {k: v for k, v in events.items() if k.startswith(key_prefix + '_')}
                total = len(route_events)
                done = sum(1 for v in route_events.values() if v)
                percent = int((done / total) * 100) if total else 0
            message['progress_bar'] = {'goal': next_goal, 'percent': percent}
            logger.debug(f"Added progress_bar for goal {next_goal}: {percent}%")
        except Exception as e:
            logger.error(f"Error adding progress information: {e}")
        
        # CRITICAL SYNCHRONIZATION UPDATE: Add render completion acknowledgment request
        message["require_ack"] = True
        
        # Convert to JSON and broadcast with retries and explicit synchronization
        try:
            message_json = json.dumps(message)
            retry_count = 0
            max_retries = 3
            
            while retry_count < max_retries:
                try:
                    # Broadcast update to all clients
                    await manager.broadcast(message_json)
                    logger.info(f"Update with timestamp {timestamp} sent to {len(manager.active_connections)} connections")
                    
                    # CRITICAL SYNCHRONIZATION UPDATE: Add delay after broadcast for client processing
                    await asyncio.sleep(0.1)
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
    
    # On WebSocket connect, start agent only if not already running
    task_exists = hasattr(app.state, 'agent_task') and app.state.agent_task is not None and not app.state.agent_task.done()
    if not task_exists:
        if app.state.dev_control:
            logger.info("WebSocket connected, starting agent in paused dev mode.")
            try:
                await start_agent()
                app.state.is_paused = True
            except Exception as e:
                logger.error(f"Error starting agent in dev mode on WebSocket connection: {e}")
        else:
            logger.info("WebSocket connected, starting agent automatically.")
            try:
                await start_agent()
            except Exception as e:
                logger.error(f"Error starting agent automatically on WebSocket connection: {e}")
    else:
        # Agent already running; maintain current pause state
        logger.info("WebSocket connected, agent already running; dev control state unchanged.")

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
                # Pass an empty string or default for grok_message here, it will be overwritten
                await send_game_updates(frame, "") 
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
                # DEBUG: log received raw message from WebSocket
                logger.debug(f"WebSocket received raw message: {raw}")
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
                
                # Handle input messages when agent is paused (dev control)
                elif mtype == "input" and getattr(app.state, 'is_paused', False) and hasattr(app.state, 'agent'):
                    button = msg.get("button")
                    logger.info(f"Dev mode: Pressed {button}")
                    if button:
                        # Flush pending queued presses (remove stale auto-press events)
                        while not queue.empty():
                            try:
                                queue.get_nowait()
                            except asyncio.QueueEmpty:
                                break
                        # Enqueue and process via shared button queue
                        await queue.put(button)
                        await process_next_button("Dev mode", app.state.agent, send_game_updates)
                        logger.info(f"Dev mode: processed button {button}")
                        # Acknowledge input after processing
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

                # Handle navigation clicks in Dev Mode
                elif mtype == "navigate" and getattr(app.state, 'is_paused', False) and hasattr(app.state, 'agent'):
                    # DEBUG: log navigate command payload
                    logger.debug(f"navigate payload: {msg}")
                    row = msg.get("row")
                    col = msg.get("col")
                    if row == 4 and col == 4:
                        logger.info("Dev mode: navigation request to self, skipping")
                        await websocket.send_text(json.dumps({"type": "tool_result", "content": "Cannot navigate to yourself."}))
                        continue
                    logger.info(f"Dev mode: navigation request to grid cell ({row}, {col})")
                    # DEBUG: log queue size before navigation
                    logger.debug(f"Queue size before navigation: {queue.qsize()}")
                    try:
                        raw_map = app.state.agent.emulator.get_collision_map()
                        # DEBUG: log collision_map dimensions
                        logger.debug(f"Raw collision_map dims: rows {len(raw_map['collision_map'])}, cols {len(raw_map['collision_map'][0])}")
                        cell = raw_map["collision_map"][row][col]
                        target_y = cell.get("y")
                        target_x = cell.get("x")
                        # DEBUG: log target coordinates obtained
                        logger.debug(f"Cell target coords: y={target_y}, x={target_x}")
                        # Use agent's navigate_to tool for pathfinding
                        class FakeToolCall:
                            def __init__(self, name, input_data, call_id=None):
                                self.name = name
                                self.input = input_data
                                self.arguments = input_data
                                self.id = call_id
                                self.type = "function"
                        # Process navigation tool call
                        result = app.state.agent.process_tool_call(
                            FakeToolCall("navigate_to", {"glob_y": target_y, "glob_x": target_x}, str(uuid.uuid4()))
                        )
                        # Execute all enqueued button presses
                        while not queue.empty():
                            await process_next_button("Agent nav", app.state.agent, send_game_updates)
                        # Broadcast tool result to all clients instead of direct send to avoid closed socket errors
                        try:
                            tool_msg = json.dumps({"type": "tool_result", "content": result})
                            await manager.broadcast(tool_msg)
                        except Exception as e:
                            logger.warning(f"Failed to broadcast tool_result: {e}")
                        # Send updated frame
                        frame = app.state.agent.get_frame()
                        if frame:
                            raw_map = app.state.agent.emulator.get_collision_map()
                            cell = raw_map["collision_map"][row][col]
                            target_y = cell.get("y")
                            target_x = cell.get("x")
                            await send_game_updates(frame, f"Dev mode: navigated to (glob_y: {target_y}, glob_x: {target_x}) (grid_y: {row}, grid_x: {col})")
                        # DEBUG: log queue size after processing navigation
                        logger.debug(f"Queue size after processing: {queue.qsize()}")
                        # Acknowledge navigation to client to stop UI from flooding
                        await websocket.send_text(json.dumps({"type": "ack", "navigate": {"row": row, "col": col}, "timestamp": int(time.time() * 1000)}))
                    except Exception as e:
                        logger.error(f"Error in dev mode navigation: {e}", exc_info=True)

                # Handle manual directional navigation helper (goDir)
                elif mtype == "navigate_to_dir" and getattr(app.state, 'is_paused', False) and hasattr(app.state, 'agent'):
                    # DEBUG: log directional navigation request
                    logger.debug(f"navigate_to_dir payload: {msg}")
                    direction = msg.get("direction")
                    logger.info(f"Dev mode: manual directional navigation request: {direction}")
                    logger.debug(f"Queue size before manual direction navigation: {queue.qsize()}")
                    try:
                        # Use agent's navigate_to tool with directional argument
                        class FakeToolCall:
                            def __init__(self, name, input_data, call_id=None):
                                self.name = name
                                self.input = input_data
                                self.arguments = input_data
                                self.id = call_id
                                self.type = "function"
                        fcall = FakeToolCall("navigate_to", {"direction": direction}, str(uuid.uuid4()))
                        result = app.state.agent.process_tool_call(fcall)
                        # Execute enqueued button presses for the navigation
                        while not queue.empty():
                            await process_next_button("Agent directional nav", app.state.agent, send_game_updates)
                        # Broadcast the tool result to clients
                        tool_msg = json.dumps({"type": "tool_result", "content": result})
                        await manager.broadcast(tool_msg)
                        # Send updated frame reflecting the move
                        frame = app.state.agent.get_frame()
                        if frame:
                            await send_game_updates(frame, f"Dev mode: navigated direction {direction}")
                        logger.debug(f"Queue size after manual direction processing: {queue.qsize()}")
                        # Acknowledge to the client
                        await websocket.send_text(json.dumps({"type": "ack", "navigate_to_dir": {"direction": direction}, "timestamp": int(time.time() * 1000)}))
                    except Exception as e:
                        logger.error(f"Error in manual directional navigation: {e}", exc_info=True)

                # Allow manual coordinate navigation via nav() helper
                elif mtype == "navigate_to_coords" and getattr(app.state, 'is_paused', False) and hasattr(app.state, 'agent'):
                    # DEBUG: log manual navigate_to_coords command payload
                    logger.debug(f"navigate_to_coords payload: {msg}")
                    gy = msg.get("glob_y")
                    gx = msg.get("glob_x")
                    logger.info(f"Dev mode: manual navigation request to global coords ({gy}, {gx})")
                    # DEBUG: log queue size before manual navigation
                    logger.debug(f"Queue size before manual navigation: {queue.qsize()}")
                    try:
                        # Use agent's navigate_to tool for pathfinding
                        class FakeToolCall:
                            def __init__(self, name, input_data, call_id=None):
                                self.name = name
                                self.input = input_data
                                self.arguments = input_data
                                self.id = call_id
                                self.type = "function"
                        result = app.state.agent.process_tool_call(
                            FakeToolCall("navigate_to", {"glob_y": gy, "glob_x": gx}, str(uuid.uuid4()))
                        )
                        # Execute all enqueued button presses
                        while not queue.empty():
                            await process_next_button("Agent nav", app.state.agent, send_game_updates)
                        # Broadcast manual tool result
                        try:
                            tool_msg = json.dumps({"type": "tool_result", "content": result})
                            await manager.broadcast(tool_msg)
                        except Exception as e:
                            logger.warning(f"Failed to broadcast manual tool_result: {e}")
                        frame = app.state.agent.get_frame()
                        if frame:
                            try:
                                await send_game_updates(frame, f"Dev mode: manual navigated to ({gy}, {gx})")
                            except Exception as e:
                                logger.warning(f"send_game_updates failed: {e}, sending update directly via websocket")
                                # Fallback: send update directly over this WebSocket
                                update_msg = {
                                    "type": "update",
                                    "timestamp": int(time.time() * 1000),
                                    "frame": base64.b64encode(frame).decode('ascii'),
                                    "frame_format": "base64",
                                    "message": f"Dev mode: manual navigated to ({gy}, {gx})"
                                }
                                try:
                                    await manager.broadcast(json.dumps(update_msg))
                                except Exception as e:
                                    logger.warning(f"Failed to broadcast fallback update_msg: {e}")
                        # DEBUG: log queue size after manual processing
                        logger.debug(f"Queue size after manual processing: {queue.qsize()}")
                        # Acknowledge manual navigation to client
                        await websocket.send_text(json.dumps({"type": "ack", "navigate_to_coords": {"glob_y": gy, "glob_x": gx}, "timestamp": int(time.time() * 1000)}))
                    except Exception as e:
                        logger.error(f"Error in manual navigation: {e}", exc_info=True)

                # Handle delay setting in Dev Mode
                elif mtype == "set_delay" and hasattr(app.state, 'agent'):
                    delay = msg.get("delay")
                    try:
                        # Update agent step delay
                        app.state.agent.step_delay = float(delay)
                        if hasattr(app.state, 'cfg'):
                            app.state.cfg.step_delay = float(delay)
                        logger.info(f"Dev mode: step_delay set to {delay}s")
                    except Exception:
                        logger.error(f"Invalid delay value received: {delay}", exc_info=True)

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
    # Resume if paused
    if app.state.agent_task is not None and not app.state.agent_task.done():
        if getattr(app.state, 'is_paused', False):
            app.state.is_paused = False
            logger.info("Dev mode deactivated: agent resumed")
            return {"status": "success", "message": "Agent resumed"}
        return {"status": "error", "message": "Agent is already running"}
    # Start the agent if not already running
    try:
        # Reset pause flag
        app.state.is_paused = False
        # Create agent if not exists
        if not hasattr(app.state, 'agent'):
            logger.error("Agent not found in app state during start request. Re-creating.")
            if not hasattr(app.state, 'cfg'):
                return {"status": "error", "message": "Config not found in app state."}
            app.state.agent = SimpleAgent(cfg=app.state.cfg, app=app)
        # Patch emulator.press_buttons once, on first agent instantiation
        from web.button_queue import queue, set_orig_press
        # Patch only once per server lifetime
        if set_orig_press and hasattr(app.state, 'agent') and getattr(app.state, '_press_patched', False) is False:
            # Save the original implementation
            set_orig_press(app.state.agent.emulator.press_buttons)
            # Override to enqueue
            def enqueue_press(buttons, wait=True):
                for b in buttons:
                    try:
                        queue.put_nowait(b)
                    except asyncio.QueueFull:
                        pass
            app.state.agent.emulator.press_buttons = enqueue_press
            # Mark patched to avoid re-patching
            app.state._press_patched = True
        # Ensure config available
        if not hasattr(app.state, 'cfg'):
            logger.error("Config not found in app state before creating agent task.")
            return {"status": "error", "message": "Config not found in app state."}
        # Launch agent task
        app.state.agent_task = asyncio.create_task(
            run_agent(
                agent=app.state.agent,
                num_steps=app.state.cfg.num_steps,
                run_log_dir=app.state.run_log_dir,
                send_game_updates=send_game_updates,
                grok_logger=app.state.grok_logger
            )
        )
        logger.info("Agent started successfully")
        return {"status": "success", "message": "Agent started successfully"}
    except Exception as e:
        logger.error(f"Error starting agent: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/pause")
async def pause_agent():
    if not hasattr(app.state, 'agent_task') or app.state.agent_task is None:
        return {"status": "error", "message": "Agent not running"}
    # Toggle pause/resume
    if getattr(app.state, 'is_paused', False):
        app.state.is_paused = False
        logger.info("Dev mode deactivated: agent resumed")
        return {"status": "success", "message": "Agent resumed"}
    else:
        app.state.is_paused = True
        logger.info("Dev mode activated: agent paused")
        return {"status": "success", "message": "Agent paused"}

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