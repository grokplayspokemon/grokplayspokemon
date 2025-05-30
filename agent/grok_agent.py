"""
Grok Agent Module - Autonomous Pokemon Playing Interface
Interfaces with XAI SDK while preserving existing play.py patterns
"""

from xai_sdk import Grok
import json
import asyncio
from typing import Dict, List, Optional, Tuple
from pathlib import Path
import time

class GrokPokemonAgent:
    def __init__(self, api_key: str, env, navigator, quest_manager):
        self.client = Grok(api_key=api_key)
        self.env = env
        self.navigator = navigator
        self.quest_manager = quest_manager
        
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
            "current_objective": None
        }
        
        # Action mapping
        self.ACTION_MAP = {
            "up": 3, "down": 0, "left": 1, "right": 2,
            "a": 4, "b": 5, "start": 7, "path": 6
        }
        
    async def get_game_state(self) -> Dict:
        """Extract text-only game state for Grok analysis"""
        obs = self.env._get_obs()
        
        # Current location
        x, y, map_id = self.env.get_game_coords()
        map_name = self.env.get_map_name_by_id(map_id)
        
        # Dialog/menu detection
        dialog = self.env.read_dialog()
        
        # Party status
        party_info = []
        for i in range(self.env.read_m("wPartyCount")):
            party_info.append({
                "species": obs["species"][i],
                "level": obs["level"][i],
                "hp": obs["hp"][i],
                "max_hp": obs["maxHP"][i]
            })
        
        # Quest status
        current_quest = getattr(self.env, 'current_loaded_quest_id', None)
        
        return {
            "location": {"x": x, "y": y, "map_id": map_id, "map_name": map_name},
            "dialog": dialog,
            "party": party_info,
            "current_quest": current_quest,
            "hp_fraction": self.env.read_hp_fraction(),
            "in_battle": self.env.read_m("wIsInBattle") > 0,
            "bag_items": list(self.env.get_items_in_bag())
        }
    
    def create_system_prompt(self) -> str:
        """Generate comprehensive system prompt for Grok"""
        return """You are playing Pokemon Red as an autonomous agent. You perceive the game through text descriptions and control it via tool calls.

AVAILABLE TOOLS:

1. press_buttons: Press game buttons in sequence
   - Parameters: {"buttons": ["a", "b", "up", "down", "left", "right", "start"]}
   - Example: {"buttons": ["up", "up", "a"]}

2. follow_path: Advance along current quest path
   - Parameters: {"steps": <number>} (default: 1)
   - This follows pre-recorded optimal paths for quests

3. update_knowledge: Store important information
   - Parameters: {"category": str, "key": str, "value": any}
   - Categories: "locations", "pokemon", "items", "npcs", "objectives"

GAME MECHANICS:
- Navigate with directional buttons
- 'A' to interact/confirm, 'B' to cancel/go back
- Start opens menu
- Path-following (tool 2) handles optimal navigation automatically

PROGRESSIVE SUMMARIZATION:
When conversation exceeds 50 turns, you'll summarize progress. Focus on:
- Major accomplishments
- Current location and objective
- Team status changes
- Key items/badges obtained

RESPONSE FORMAT:
Always structure responses as:
1. Observation about current state
2. Reasoning about next action
3. Tool call(s) to execute
"""

    async def get_grok_action(self, game_state: Dict) -> List[Dict]:
        """Query Grok for next action based on game state"""
        
        # Construct user message
        user_message = f"""Current game state:
Location: {game_state['location']['map_name']} at ({game_state['location']['x']}, {game_state['location']['y']})
Quest: {game_state['current_quest']}
Dialog: {game_state['dialog'] or 'None'}
In Battle: {game_state['in_battle']}
Party HP: {game_state['hp_fraction']:.2%}

What should I do next?"""

        # Check for summarization trigger
        if self.turn_count >= self.max_turns:
            summary = await self._progressive_summarization()
            self.conversation_history = [{"role": "assistant", "content": summary}]
            self.turn_count = 0
        
        # Add to conversation
        self.conversation_history.append({"role": "user", "content": user_message})
        
        # Query Grok
        response = await self.client.create_chat_completion(
            model="grok-2",
            messages=[
                {"role": "system", "content": self.create_system_prompt()},
                *self.conversation_history
            ],
            temperature=0.7,
            max_tokens=500
        )
        
        # Parse response and extract tool calls
        grok_response = response.choices[0].message
        self.conversation_history.append(grok_response)
        self.turn_count += 1
        
        return self._parse_tool_calls(grok_response)
    
    def _parse_tool_calls(self, response) -> List[Dict]:
        """Extract and validate tool calls from Grok response"""
        tool_calls = []
        
        if hasattr(response, 'tool_calls'):
            for call in response.tool_calls:
                tool_calls.append({
                    "name": call.function.name,
                    "arguments": json.loads(call.function.arguments)
                })
        
        return tool_calls
    
    async def execute_tool_calls(self, tool_calls: List[Dict]) -> List[int]:
        """Convert tool calls to button press actions"""
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
                self._update_knowledge_base(
                    call["arguments"]["category"],
                    call["arguments"]["key"],
                    call["arguments"]["value"]
                )
        
        return actions
    
    def _update_knowledge_base(self, category: str, key: str, value):
        """Update internal knowledge base"""
        if category == "locations":
            self.knowledge_base["locations_visited"].add(value)
        elif category == "pokemon" and key:
            self.knowledge_base["pokemon_team"][key] = value
        elif category == "items":
            self.knowledge_base["items_obtained"].add(value)
        elif category == "npcs":
            self.knowledge_base["npcs_encountered"].add(value)
        elif category == "objectives":
            self.knowledge_base["current_objective"] = value
    
    async def _progressive_summarization(self) -> str:
        """Generate summary of recent progress"""
        summary_prompt = """Summarize the last 50 turns of gameplay. Include:
- Major progress and accomplishments
- Current location and immediate objective
- Pokemon team changes
- Important items or badges obtained
- Any significant battles or encounters"""
        
        response = await self.client.create_chat_completion(
            model="grok-2", 
            messages=[
                {"role": "system", "content": "Summarize Pokemon gameplay progress concisely."},
                {"role": "user", "content": summary_prompt},
                *self.conversation_history[-10:]  # Last 10 messages for context
            ],
            max_tokens=300
        )
        
        return response.choices[0].message.content