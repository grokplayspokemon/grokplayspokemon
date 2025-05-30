"""
Grok Integration Module - Fully Modular Autonomous Pokemon Player
Provides complete isolation of Grok logic from main play.py
"""

import asyncio
import json
import threading
import time
from typing import Dict, List, Optional, Tuple, Any
from pathlib import Path
from dataclasses import dataclass, asdict
from datetime import datetime
import queue

# XAI SDK import
try:
    from xai_sdk import Grok
except ImportError:
    print("WARNING: xai_sdk not installed. Install with: pip install xai-sdk")
    Grok = None

# Web server imports for UI updates
from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
import aiofiles

@dataclass
class GameState:
    """Structured game state for UI updates"""
    location: Dict[str, Any]
    quest_id: Optional[int]
    party: List[Dict[str, Any]]
    dialog: Optional[str]
    in_battle: bool
    hp_fraction: float
    money: int
    badges: int
    pokedex_seen: int
    pokedex_caught: int
    steps: int
    items: List[str]
    
class GrokIntegration:
    """Complete Grok integration handler"""
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.env = None
        self.navigator = None
        self.quest_manager = None
        self.running = False
        
        # Initialize Grok client
        if Grok:
            self.client = Grok(api_key=api_key)
        else:
            self.client = None
            
        # Action queue for game integration
        self.action_queue = queue.Queue()
        
        # UI update queue
        self.ui_queue = asyncio.Queue()
        
        # Conversation management
        self.conversation_history = []
        self.max_turns = 50
        self.turn_count = 0
        
        # Knowledge base
        self.knowledge_base = {
            "locations_visited": set(),
            "pokemon_team": {},
            "items_obtained": set(),
            "npcs_encountered": set(),
            "current_objective": None,
            "quest_completions": {}
        }
        
        # Token tracking
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        
        # Action mapping
        self.ACTION_MAP = {
            "up": 3, "down": 0, "left": 1, "right": 2,
            "a": 4, "b": 5, "start": 7, "path": 6
        }
        
        # Web server
        self.app = FastAPI()
        self.setup_routes()
        
    def setup_routes(self):
        """Configure web server routes"""
        
        @self.app.get("/")
        async def read_index():
            # Serve the UI index.html from the correct location
            index_path = Path(__file__).parent.parent / "web" / "templates" / "index.html"
            async with aiofiles.open(index_path, mode='r') as f:
                html = await f.read()
            return HTMLResponse(content=html)
        
        @self.app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            await websocket.accept()
            try:
                while True:
                    # Send queued updates to UI
                    if not self.ui_queue.empty():
                        update = await self.ui_queue.get()
                        await websocket.send_json(update)
                    await asyncio.sleep(0.1)
            except Exception:
                pass
                
        @self.app.get("/events")
        async def events():
            """Server-sent events endpoint"""
            async def event_generator():
                while True:
                    try:
                        if not self.ui_queue.empty():
                            update = await self.ui_queue.get()
                            print(f"SSE /events (fastapi): {json.dumps(update)}")
                            yield f"data: {json.dumps(update)}\n\n"
                        await asyncio.sleep(0.1)
                    except Exception:
                        break
            return StreamingResponse(event_generator(), media_type="text/event-stream")
        
        @self.app.get("/required_completions.json")
        async def get_quests():
            p = Path(__file__).parent / "environment_helpers" / "required_completions.json"
            return FileResponse(p, media_type="application/json")
        
        # Mount static assets for Grok UI, if the directory exists
        static_dir = Path(__file__).parent.parent / "web" / "static"
        if static_dir.is_dir():
            self.app.mount(
                "/static",
                StaticFiles(directory=static_dir),
                name="static"
            )
        else:
            print(f"GrokIntegration: static directory {static_dir} not found; skipping static mount")
    
    def initialize(self, env, navigator, quest_manager):
        """Initialize with game components"""
        self.env = env
        self.navigator = navigator
        self.quest_manager = quest_manager
        self.running = True
        
        # Start Grok decision loop
        self.grok_thread = threading.Thread(target=self._run_grok_loop, daemon=True)
        self.grok_thread.start()
        
        # Start UI update loop
        self.ui_thread = threading.Thread(target=self._run_ui_loop, daemon=True)
        self.ui_thread.start()
        
        # Start web server
        self.web_thread = threading.Thread(target=self._run_web_server, daemon=True)
        self.web_thread.start()
        
    def _run_web_server(self):
        """Run FastAPI server in thread"""
        print("GrokIntegration: launching FastAPI server on http://0.0.0.0:8080")
        uvicorn.run(self.app, host="0.0.0.0", port=8080, log_level="info")
        
    def _run_grok_loop(self):
        """Main Grok decision loop"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._grok_decision_loop())
        
    def _run_ui_loop(self):
        """UI update loop"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._ui_update_loop())
        
    async def _grok_decision_loop(self):
        """Async Grok decision making"""
        while self.running:
            try:
                if not self.client:
                    await asyncio.sleep(5)
                    continue
                    
                # Get game state
                game_state = self._extract_game_state()
                
                # Update UI with current state
                await self.ui_queue.put({
                    "type": "game_state",
                    "data": asdict(game_state)
                })
                
                # Get Grok decision
                tool_calls = await self._get_grok_decision(game_state)
                
                # Convert to actions
                actions = self._convert_tool_calls_to_actions(tool_calls)
                
                # Queue actions
                for action in actions:
                    self.action_queue.put(action)
                    
                # Delay between decisions
                await asyncio.sleep(1.0)
                
            except Exception as e:
                print(f"Grok decision error: {e}")
                await asyncio.sleep(5)
                
    async def _ui_update_loop(self):
        """Continuous UI updates"""
        while self.running:
            try:
                # Extract current game state
                state = self._extract_game_state()
                
                # Format location update
                await self.ui_queue.put({
                    "type": "location",
                    "data": {
                        "gx": state.location["gx"],
                        "gy": state.location["gy"],
                        "map_id": state.location["map_id"],
                        "map_name": state.location["map_name"]
                    }
                })
                
                # Quest update
                await self.ui_queue.put({
                    "type": "current_quest",
                    "data": f"Quest {state.quest_id:03d}" if state.quest_id else "N/A"
                })
                
                # Pokemon team update
                await self.ui_queue.put({
                    "type": "pokemon_team",
                    "data": state.party
                })
                
                # Stats update
                await self.ui_queue.put({
                    "type": "stats",
                    "data": {
                        "money": state.money,
                        "badges": state.badges,
                        "pokedex": f"{state.pokedex_caught}/{state.pokedex_seen}",
                        "steps": state.steps
                    }
                })
                
                # LLM usage update
                await self.ui_queue.put({
                    "type": "llm_usage",
                    "data": {
                        "input_tokens": self.total_input_tokens,
                        "output_tokens": self.total_output_tokens
                    }
                })
                
                await asyncio.sleep(0.5)
                
            except Exception as e:
                print(f"UI update error: {e}")
                await asyncio.sleep(1)
    
    def _extract_game_state(self) -> GameState:
        """Extract comprehensive game state"""
        try:
            obs = self.env._get_obs()
            x, y, map_id = self.env.get_game_coords()
            
            # Global coordinates
            from data.recorder_data.global_map import local_to_global
            gy, gx = local_to_global(y, x, map_id)
            
            # Extract party with full details
            party = []
            for i in range(self.env.read_m("wPartyCount")):
                pokemon = {
                    "id": obs["species"][i],
                    "species": obs["species"][i],
                    "nickname": f"MON{i+1}",  # Would need to read actual nickname
                    "level": obs["level"][i],
                    "hp": obs["hp"][i],
                    "maxHp": obs["maxHP"][i],
                    "types": self._get_pokemon_types(obs["type1"][i], obs["type2"][i]),
                    "status": self._get_status_string(obs["status"][i])
                }
                party.append(pokemon)
            
            # Money calculation
            money = 0
            for i in range(3):
                money = money * 100 + int(self.env.read_m(0xD347 + i) >> 4) * 10
                money += int(self.env.read_m(0xD347 + i) & 0xF)
            
            # Pokedex stats
            pokedex_seen = sum(self.env.seen_pokemon)
            pokedex_caught = sum(self.env.caught_pokemon)
            
            return GameState(
                location={
                    "x": x, "y": y, "gx": gx, "gy": gy,
                    "map_id": map_id,
                    "map_name": self.env.get_map_name_by_id(map_id)
                },
                quest_id=getattr(self.env, 'current_loaded_quest_id', None),
                party=party,
                dialog=self.env.read_dialog(),
                in_battle=self.env.read_m("wIsInBattle") > 0,
                hp_fraction=self.env.read_hp_fraction(),
                money=money,
                badges=self.env.get_badges(),
                pokedex_seen=pokedex_seen,
                pokedex_caught=pokedex_caught,
                steps=self.env.step_count,
                items=[item.name for item in self.env.get_items_in_bag()]
            )
        except Exception as e:
            print(f"Error extracting game state: {e}")
            # Return minimal state
            return GameState(
                location={"x": 0, "y": 0, "gx": 0, "gy": 0, "map_id": 0, "map_name": "Unknown"},
                quest_id=None, party=[], dialog=None, in_battle=False,
                hp_fraction=1.0, money=0, badges=0, pokedex_seen=0,
                pokedex_caught=0, steps=0, items=[]
            )
    
    def _get_pokemon_types(self, type1: int, type2: int) -> List[str]:
        """Convert type IDs to strings"""
        type_map = {
            0x00: "normal", 0x01: "fighting", 0x02: "flying", 0x03: "poison",
            0x04: "ground", 0x05: "rock", 0x07: "bug", 0x08: "ghost",
            0x14: "fire", 0x15: "water", 0x16: "grass", 0x17: "electric",
            0x18: "psychic", 0x19: "ice", 0x1A: "dragon"
        }
        types = []
        if type1 in type_map:
            types.append(type_map[type1])
        if type2 != type1 and type2 in type_map:
            types.append(type_map[type2])
        return types
    
    def _get_status_string(self, status: int) -> str:
        """Convert status byte to string"""
        if status == 0:
            return "OK"
        elif status & 0x08:
            return "PSN"
        elif status & 0x10:
            return "BRN"
        elif status & 0x20:
            return "FRZ"
        elif status & 0x40:
            return "PAR"
        elif status & 0x07:
            return "SLP"
        return "???"
    
    async def _get_grok_decision(self, game_state: GameState) -> List[Dict]:
        """Query Grok for decision"""
        # Check for summarization
        if self.turn_count >= self.max_turns:
            summary = await self._progressive_summarization()
            self.conversation_history = [{"role": "assistant", "content": summary}]
            self.turn_count = 0
        
        # Create prompt
        prompt = self._create_game_state_prompt(game_state)
        self.conversation_history.append({"role": "user", "content": prompt})
        
        # Query Grok
        response = await self.client.create_chat_completion(
            model="grok-2",
            messages=[
                {"role": "system", "content": self._get_system_prompt()},
                *self.conversation_history
            ],
            temperature=0.7,
            max_tokens=500
        )
        
        # Track tokens
        self.total_input_tokens += response.usage.prompt_tokens
        self.total_output_tokens += response.usage.completion_tokens
        
        # Parse response
        grok_response = response.choices[0].message
        self.conversation_history.append(grok_response)
        self.turn_count += 1
        
        # Update UI with Grok thinking
        await self.ui_queue.put({
            "type": "grok_thinking",
            "data": grok_response.content[:200]
        })
        
        return self._parse_tool_calls(grok_response)
    
    def _create_game_state_prompt(self, state: GameState) -> str:
        """Format game state for Grok"""
        return f"""Current game state:
Location: {state.location['map_name']} at ({state.location['x']}, {state.location['y']})
Global: ({state.location['gx']}, {state.location['gy']})
Quest: {state.quest_id}
Dialog: {state.dialog or 'None'}
In Battle: {state.in_battle}
Party HP: {state.hp_fraction:.2%}
Money: ₽{state.money}
Badges: {state.badges}

Party:
{self._format_party(state.party)}

What action should I take?"""
    
    def _format_party(self, party: List[Dict]) -> str:
        """Format party for display"""
        lines = []
        for p in party:
            lines.append(f"- {p['nickname']} (Lv.{p['level']}) HP: {p['hp']}/{p['maxHp']} Status: {p['status']}")
        return "\n".join(lines)
    
    def _get_system_prompt(self) -> str:
        """System prompt for Grok"""
        return """You are playing Pokemon Red. Control the game through tool calls.

TOOLS:
1. press_buttons: {"buttons": ["a", "b", "up", "down", "left", "right", "start"]}
2. follow_path: {"steps": <number>} - Follow quest path
3. update_knowledge: {"category": str, "key": str, "value": any}

OBJECTIVE: Progress through the game efficiently. Follow quest paths when available."""
    
    def _parse_tool_calls(self, response) -> List[Dict]:
        """Extract tool calls from response"""
        tool_calls = []
        if hasattr(response, 'tool_calls') and response.tool_calls:
            for call in response.tool_calls:
                tool_calls.append({
                    "name": call.function.name,
                    "arguments": json.loads(call.function.arguments)
                })
        return tool_calls
    
    def _convert_tool_calls_to_actions(self, tool_calls: List[Dict]) -> List[int]:
        """Convert tool calls to button presses"""
        actions = []
        for call in tool_calls:
            if call["name"] == "press_buttons":
                for button in call["arguments"]["buttons"]:
                    if button.lower() in self.ACTION_MAP:
                        actions.append(self.ACTION_MAP[button.lower()])
            elif call["name"] == "follow_path":
                steps = call["arguments"].get("steps", 1)
                for _ in range(steps):
                    actions.append(self.ACTION_MAP["path"])
            elif call["name"] == "update_knowledge":
                self._update_knowledge(
                    call["arguments"]["category"],
                    call["arguments"]["key"],
                    call["arguments"]["value"]
                )
        return actions
    
    def _update_knowledge(self, category: str, key: str, value: Any):
        """Update knowledge base"""
        if category in self.knowledge_base:
            if isinstance(self.knowledge_base[category], set):
                self.knowledge_base[category].add(value)
            elif isinstance(self.knowledge_base[category], dict):
                self.knowledge_base[category][key] = value
            else:
                self.knowledge_base[category] = value
    
    async def _progressive_summarization(self) -> str:
        """Generate progress summary"""
        summary_prompt = "Summarize the last 50 turns of Pokemon gameplay."
        
        response = await self.client.create_chat_completion(
            model="grok-2",
            messages=[
                {"role": "system", "content": "Summarize concisely."},
                {"role": "user", "content": summary_prompt},
                *self.conversation_history[-10:]
            ],
            max_tokens=300
        )
        
        return response.choices[0].message.content
    
    def get_next_action(self) -> Optional[int]:
        """Get next action from queue (called by play.py)"""
        try:
            return self.action_queue.get_nowait()
        except queue.Empty:
            return None
    
    def shutdown(self):
        """Clean shutdown"""
        self.running = False

# Singleton instance
_grok_instance = None

def initialize_grok(api_key: str, env, navigator, quest_manager) -> GrokIntegration:
    """Initialize Grok integration"""
    global _grok_instance
    if not _grok_instance:
        _grok_instance = GrokIntegration(api_key)
        _grok_instance.initialize(env, navigator, quest_manager)
    return _grok_instance

def get_grok_action() -> Optional[int]:
    """Get next Grok action"""
    if _grok_instance:
        return _grok_instance.get_next_action()
    return None

def shutdown_grok():
    """Shutdown Grok integration"""
    global _grok_instance
    if _grok_instance:
        _grok_instance.shutdown()
        _grok_instance = None