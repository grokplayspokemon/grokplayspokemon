# simple_agent.py
import logging
logger = logging.getLogger(__name__)

import copy
import io
import json
import os
import requests
import time
import uuid
import re
import time
import random
from pathlib import Path

from agent.prompts import SYSTEM_PROMPT, SUMMARY_PROMPT
from agent.tools import AVAILABLE_TOOLS
from agent.emulator import Emulator
from game_data.nav import Nav
from agent.memory_reader import PokemonType
try:
    from bin.ram_reader.red_memory_battle import *
    from bin.ram_reader.red_memory_env import *
    from bin.ram_reader.red_memory_items import *
    from bin.ram_reader.red_memory_map import *
    from bin.ram_reader.red_memory_menus import *
    from bin.ram_reader.red_memory_player import *
    from bin.ram_reader.red_ram_debug import *
    from pyboy.utils import WindowEvent
except ImportError as e:
    logger.warning(f"Could not import RAM reader modules: {e}")
    # Fallback or alternative handling if needed

from bin.red_pyboy_manager import PyBoyManager
from openai import OpenAI
import game_data.ram_map_leanke as ram_map

        
def build_xai_toolspec():
    tool_specs = []
    for t in AVAILABLE_TOOLS:
        tool_specs.append({
            "type": "function",
            "function": {
                "name":        t["name"],
                "description": t["description"],
                "parameters":  t["input_schema"],
            },
        })
    return tool_specs


class MemoryAdapter:
    def __init__(self, emulator):
        self.emulator = emulator
        self.memory = emulator.pyboy.memory
        self.symbol_lookup = emulator.pyboy.symbol_lookup

    def get_memory_value(self, addr):
        if isinstance(addr, str):
            _, addr = self.symbol_lookup(addr)
        return self.memory[addr]
    
class FakeToolCall:
    """
    Utility class that provides standardized tool call interface for internal system functions.
    Simulates external API structure for compatibility with process_tool_call() method.
    """
    def __init__(self, name, input_data, id):
        """
        Initialize tool call with required attributes.
        
        Parameters:
            name (str): Tool identifier 
            input_data (dict): Parameter dictionary for tool execution
            id (str): Unique identifier for call instance
        """
        self.name = name
        self.input = input_data
        self.id = id

class SimpleAgent:
    def __init__(self, cfg, app=None):
        """Initialize the simple agent focused on exploration rewards."""
        self.emulator = Emulator(cfg.rom_path, cfg.emulator_headless, cfg.emulator_sound)
        self.emulator.initialize()
        self.emulator.register_hooks()
        self.nav = Nav(self.emulator)
        
        # Initialize PyBoyManager with the Emulator instance
        try:
            self.pyboy_manager = PyBoyManager(cfg, emulator=self.emulator)
        except ImportError:
            logger.warning("PyBoyManager could not be imported, proceeding without it.")
            self.pyboy_manager = None
        
        # Set Grok provider settings
        self.model_name = cfg.llm_model
        self.temperature = cfg.llm_temperature
        self.max_tokens = cfg.llm_max_tokens
        self.xai_tools = build_xai_toolspec()
        # Store which provider we're using
        self.llm_provider = cfg.llm_provider
        
        # Get Grok API key
        self.xai_api_key = os.getenv("XAI_API_KEY")
        if not self.xai_api_key:
            raise ValueError("XAI_API_KEY environment variable not set for Grok provider")
        
        # Agent state
        self.running = True
        self.max_history = cfg.max_history
        self.last_message = "Game starting..."
        self.app = app
        self.step_delay = getattr(cfg, 'step_delay', 0.1)
        self.history_summary = None
        self.latest_game_state = None
        
        # Reward system
        self.episode_step = 0
        self.episode_count = 0
        self.current_episode_reward = 0.0
        self.episode_rewards = []
        self.visited_tiles = set()
        self.interacted_npcs: set[tuple[int, int]] = set()  # Track interacted NPCs by global (x, y)
        self.best_reward = 0.0
        self.total_unique_tiles = 0
        self.step_rewards = []  # Track individual step rewards
        
        # Battle tracking
        self.was_in_battle = False
        self.currently_in_battle = False  # Track if currently in battle state
        self._last_battled_npc_key: tuple[int, int] | None = None # Track (map_id, sprite_index) of the last NPC battled
        # Track last battle moves and selection for UI display
        self.last_move_list: list[str] = []
        self.last_chosen_move_index: int | None = None
        self.last_chosen_move_name: str | None = None
        
        # New: Menu and stuck detection tracking
        self.last_location = None
        self.persistent_location_count = 0
        self.consecutive_zero_rewards = 0
        self.last_tool_type = None
        self.same_button_press_count = 0
        self.last_button_pressed = None
        
        # System prompt
        self.system_prompt = SYSTEM_PROMPT

        # Message history
        self.message_history: list[dict] = [
        { "role": "system",  "content": [{"type":"text","text": self._get_system_prompt()}] },
        { "role": "user",    "content": [{"type":"text","text": ""}] }
        ]

        # Initialize tracking
        self.prev_dialog_text = None
        self.completed_steps = []
        self.map_stack = []
        self.move_log = []
        self.current_map = None
            
        # Event tracking state
        self.event_tracking_enabled = True
        self.last_event_check_time = 0
        self.event_check_interval = 5  # Check events every 5 seconds
        self.game_progression = None
        
        cfg.debug = False  # or True, as appropriate
        cfg.extra_buttons = False  # or True, as appropriate
    
    def determine_optimal_tool_choice(self):
        """Determine which tool to force based on current game state."""
        # Check for battles first - highest priority
        if self.might_be_battle() and self.is_in_non_navigation_state():
            pass
        #     # If in battle, prioritize handle_battle
        #     logger.info("[Tool Selection] Battle detected: using handle_battle tool")
        #     return "handle_battle"    
        # # Check if in dialog or menu state - second priority
        # # elif self.is_in_non_navigation_state():
        # #     # If in dialog/menu, prioritize exit_menu
        # #     return "exit_menu"
        else:   
            # Default to press_buttons as the most commonly used tool
            return "press_buttons"
    

    def _to_plain_dict(self, msg):
        """
        Accepts ChatCompletionMessage, dict, or pydantic model and
        returns a plain serialisable dict with 'role' and 'content'.
        """
        if isinstance(msg, dict):
            return msg
        # pydantic BaseModel has model_dump(); fallback to built‑ins
        if hasattr(msg, "model_dump"):
            return msg.model_dump()
        if hasattr(msg, "__dict__"):
            # ChatCompletionMessage: role, content, tool_calls, etc.
            return {k: getattr(msg, k) for k in msg.__dict__ if not k.startswith("_")}
        raise TypeError(f"Cannot coerce {type(msg)} to dict")

    def sanitize_message_history(self):
        """
        Strict deduplication and normalization of message history to prevent context pollution
        and eliminate redundant game state transmissions.
        """
        # Initialize tracking containers
        cleaned = []
        seen_content_hashes = set()
        system_message_found = False
        
        # Process each message with duplication prevention
        for m in self.message_history:
            # Skip null entries
            if m is None:
                continue
            
            # Normalize message format
            if isinstance(m, dict):
                message = m
            elif hasattr(m, "model_dump"):
                message = m.model_dump()
            elif hasattr(m, "__dict__"):
                message = {k: getattr(m, k) for k in m.__dict__ if not k.startswith("_")}
            else:
                logger.debug(f"Skipping message with unsupported type: {type(m)}")
                continue
            
            # Enforce structural integrity
            if "role" not in message:
                continue
            
            # Process by role with deduplication
            if message["role"] == "system":
                if not system_message_found:
                    cleaned.append(message)
                    system_message_found = True
            elif message["role"] == "user":
                # Create content fingerprint for deduplication
                if "content" in message:
                    content_str = str(message["content"])
                    content_hash = hash(content_str)
                    
                    if content_hash not in seen_content_hashes:
                        seen_content_hashes.add(content_hash)
                        cleaned.append(message)
                    else:
                        logger.debug(f"Removing duplicate user message")
            elif message["role"] in ["assistant", "tool"]:
                # Preserve tool responses and assistant messages
                # Tool responses are typically unique by ID
                cleaned.append(message)
        
        # Ensure system message exists
        if not system_message_found:
            cleaned.insert(0, {
                "role": "system", 
                "content": [{"type": "text", "text": self._get_system_prompt()}]
            })
        
        # Ensure at least one user message exists
        if not any(m["role"] == "user" for m in cleaned):
            cleaned.append({
                "role": "user",
                "content": [{"type": "text", "text": "Explore the game world to maximize rewards."}]
            })
        
        # Truncate long history: if beyond max_history, summarize and reset
        if getattr(self, 'max_history', None) and len(cleaned) > self.max_history:
            logger.info(f"Message history length {len(cleaned)} exceeds max_history {self.max_history}, summarizing.")
            # summarization resets message_history
            self.summarize_history()
            return
        # Update message history with optimized version
        self.message_history = cleaned
        
        # Log optimization metrics
        logger.debug(f"Sanitized message history: {len(cleaned)} messages ({len(seen_content_hashes)} unique contents)")

    
    def _get_system_prompt(self) -> str:
        """Get the core system prompt with tool usage instructions."""
        prompt = self.system_prompt
        
        # Add explicit reasoning constraints
        prompt += "\n\nREASONING CONSTRAINTS: Focus ONLY on immediate gameplay actions. Do NOT engage in lengthy deliberations about game mechanics, hypothetical scenarios, or unrelated topics. Limit internal reasoning EXCLUSIVELY to deciding the next optimal action based on current game state. Do not reason about topics unrelated to Pokémon."
        
        # Add specific tool usage instructions
        prompt += "\n\nCRITICAL INSTRUCTION: You MUST use the provided tools for all actions. DO NOT suggest actions in text form. Always use the tools explicitly.\n"
        
        # # List available tools with clear usage examples
        # prompt += "\nAvailable tools:\n"
        # prompt += "1. navigate_to: Use to move to a specific grid position (e.g., {\"row\": 3, \"col\": 4}). You will use this most of all because most of the game is exploring.\n"
        # prompt += "2. press_buttons: Press emulator buttons. Available buttons: \'a\', \'b\', \'start\', \'up\', \'down\', \'left\', \'right\'. Use this to move around.\n"
        # prompt += "3. exit_menu: Use to exit any active menu or dialog quickly.\n"
        # prompt += "4. handle_battle: Use to handle battle situations when you are in a battle.\n"
        # prompt += "5. exit_to_last_map: Use to return to previous map when you are in a building.\n"
        
        return prompt

    def update_event_tracking(self):
        """Updates event tracking information for LLM guidance."""
        try:
            # Analyze game progression using ram_map integration
            self._cached_event_status = self.analyze_game_progression()
            # Expose cached status to emulator for state info exposure
            setattr(self.emulator, '_cached_event_status', self._cached_event_status)
            # Format event progression data for LLM consumption
            progression_summary = self.format_event_progression(self._cached_event_status)
            # Update history summary and agent state
            self.history_summary = progression_summary
            self.game_progression = self._cached_event_status
            logger.info("Event tracking updated")
            logger.debug(f"Current progression: {progression_summary[:200]}...")
        except Exception as e:
            logger.error(f"Failed to update event tracking: {e}", exc_info=True)

    def format_event_progression(self, progression):
        """Formats progression data into a concise summary for the LLM."""
        if not progression:
            return "No progression data available."
            
        summary = "## GAME PROGRESSION SUMMARY ##\n\n"
        
        # Location info
        summary += f"Current Location: {progression['current_location']} (Map ID: {progression['map_id']})\n"
        
        # Badge progress
        badges = progression['badges']
        summary += f"Badges: {', '.join(badges) if badges else 'None yet'}\n\n"
        
        # Next steps (most important for LLM guidance)
        summary += "RECOMMENDED NEXT ACTIONS:\n"
        for i, step in enumerate(progression['recommended_next_steps'], 1):
            summary += f"{i}. {step}\n"
        
        # Area-specific progress relevant to current location
        current_loc = progression['current_location']
        
        # Add information about nearby relevant areas
        # This helps the LLM understand what it should be doing in the current area
        for area_name, area_data in progression['major_areas'].items():
            # Simple heuristic to determine if area is relevant to current location
            if area_name.lower() in current_loc.lower() or self.is_area_nearby(area_name, current_loc):
                summary += f"\n{area_name.upper()} STATUS:\n"
                completed = []
                in_progress = []
                
                for event_name, status in area_data.items():
                    if status > 0:
                        completed.append(event_name)
                    elif self.is_event_available(event_name, progression):
                        in_progress.append(event_name)
                
                if completed:
                    summary += f"- Completed: {', '.join(completed)}\n"
                if in_progress:
                    summary += f"- Available: {', '.join(in_progress)}\n"
        
        return summary

    def is_area_nearby(self, area_name, current_location):
        """Determines if an area is relevant to the current location based on game geography."""
        # Mapping of locations that are considered "nearby" or relevant
        nearby_map = {
            "silph_co": ["SAFFRON", "SILPH"],
            "rock_tunnel": ["ROCK TUNNEL", "ROUTE 10", "LAVENDER"],
            "poke_tower": ["LAVENDER", "POKEMON TOWER"],
            "rocket_hideout": ["CELADON", "GAME CORNER"],
            # Add more mappings...
        }
        
        relevant_locations = nearby_map.get(area_name.lower(), [])
        return any(loc.lower() in current_location.lower() for loc in relevant_locations)

    def is_event_available(self, event_name, progression):
        """
        Determines if an event is currently available based on game state.
        Uses game logic rules to check if prerequisites are met.
        """
        # Example logic for event availability
        if "beat_ghost_marowak" in event_name:
            # Check if player has Silph Scope
            return "Silph Scope" in progression.get("items", [])
        
        # More event availability logic...
        
        # Default to True - assume event is available unless proven otherwise
        return True
   
    def format_game_state(self) -> str:
        """
        Format the current game state in a way that's optimized for LLM processing.
        Now includes event progression information.
        """
        # Start with explicit header
        state_parts = ["## CURRENT GAME STATE ##"]
        
        # Add reward information prominently at the top
        state_parts.append("REWARD STATUS:")
        state_parts.append(f"- Episode Reward: {self.current_episode_reward:.2f}")
        state_parts.append(f"- Episode Step: {self.episode_step}/30")
        state_parts.append(f"- Unique Tiles Explored: {len(self.visited_tiles)}")
        if self.episode_rewards:
            state_parts.append(f"- Previous Episode Rewards: {', '.join([f'{r:.2f}' for r in self.episode_rewards])}")
        
        # Add game state information
        if self.latest_game_state:
            state_parts.append("\nGAME ENVIRONMENT:")
            state_parts.append(self.latest_game_state)
        
        # Add explicit detection of special states
        if self.is_in_non_navigation_state():
            state_parts.append("\nSPECIAL STATE DETECTED:")
            state_parts.append("⚠️ DIALOG/MENU ACTIVE - No penalties applied")
            # state_parts.append("Use exit_menu tool to clear dialogs/menus")
            
            # Add battle detection
            if self.currently_in_battle:
                state_parts.append("⚠️ CURRENTLY IN BATTLE!")
        
        # Add event tracking information - NEW SECTION
        if self.game_progression:
            state_parts.append("\nGAME PROGRESSION:")
            
            # Add badges collected
            badges = self.game_progression.get("badges", [])
            state_parts.append(f"- Badges: {', '.join(badges) if badges else 'None yet'}")
            
            # Add next steps (most important for guidance)
            next_steps = self.game_progression.get("recommended_next_steps", [])
            if next_steps:
                state_parts.append("\nRECOMMENDED NEXT ACTIONS:")
                for i, step in enumerate(next_steps, 1):
                    state_parts.append(f"{i}. {step}")
            
            # Add relevant events for current area
            current_loc = self.game_progression.get("current_location", "")
            state_parts.append(f"\nCURRENT AREA ({current_loc}) EVENTS:")
            
            # Find relevant area data based on current location
            for area_name, area_data in self.game_progression.get("major_areas", {}).items():
                if area_name.lower() in current_loc.lower() or self.is_area_nearby(area_name, current_loc):
                    # Add incomplete events for the current area
                    incomplete_events = []
                    for event_name, status in area_data.items():
                        if status == 0 and self.is_event_available(event_name, self.game_progression):
                            incomplete_events.append(event_name)
                    
                    if incomplete_events:
                        state_parts.append(f"- Available Events: {', '.join(incomplete_events[:3])}")
        
        # Add history summary if available
        if self.history_summary:
            state_parts.append("\nHISTORY SUMMARY:")
            state_parts.append(self.history_summary)
        
        return "\n".join(state_parts)

    def calculate_step_reward(self, current_position):
        """Calculate reward for the current step based on exploration.
        No penalties are applied during dialogs, menus, or battles.
        """
        # Check if in dialog, menu or battle state
        if self.is_in_non_navigation_state():
            return 0.0  # No reward or penalty during non-navigation states
            
        x, y, map_id = current_position
        position_key = (x, y, map_id)
        
        # Check if this is a new tile
        if position_key not in self.visited_tiles:
            self.visited_tiles.add(position_key)
            return 0.01  # Reward for new tile
        else:
            return -0.02  # Penalty for revisiting
            
    def is_in_non_navigation_state(self):
        """Check if player is currently in a dialog, menu, or battle state."""
        # Check for dialog
        dialog = self.emulator.get_active_dialog()
        
        # If something is in the dialog, it is a menu, sign, NPC interaction, or battle.
        if dialog is not None and dialog != "None" and dialog != " None" and dialog != "":
            # Enhanced dialog detection with menu keywords
            menu_indicators = [
                "POKéDEX", "POKéMON", "ITEM", "SAVE", "OPTION", "EXIT",
                "►", "MENU", "PC", "BADGE", "MAP", "TRAINER"
            ]
            
            indicator_count = sum(1 for keyword in menu_indicators if keyword in dialog)
            if indicator_count >= 2:  # If multiple menu keywords detected, definitely in a menu
                matches = [kw for kw in menu_indicators if kw in dialog]
                logger.info(f"[Dialog Detection] Menu state detected with {indicator_count} indicators: {', '.join(matches)}")
                self.in_menu = True
                return True
            
            # Standard dialog check
            return True
            
        # Additional checks for menu state:
        # 1. Check if game coords haven't changed despite multiple movement attempts
        # 2. Check if reward is consistently 0.00 or negative despite movement attempts
        if hasattr(self, 'persistent_location_count') and self.persistent_location_count > 3:
            logger.info(f"Persistent location detected: this tile has been visited {self.persistent_location_count} times")
            return False
            
        # For now, just check dialog with enhanced detection
        return False
        
    def might_be_battle(self):
        """Determine if current dialog might be a battle dialog based on content.
        
        This method checks for the battle UI pattern and battle-related terms.
        """
        # There will always be a dialog if there is a battle.
        dialog = self.emulator.get_active_dialog()
        if not dialog:
            self.currently_in_battle = False
            return False
            
        elif dialog:
            # Stays True until out of battle.
            if self.currently_in_battle:
                return True
            # Primary battle indicator: FIGHT menu option in the battle UI
            if "►FIGHT" in dialog:
                logger.info("[Battle Detection] Battle UI detected with ►FIGHT menu")
                self.currently_in_battle = True
                return True
                
            # Secondary battle UI pattern detection (include move options)
            battle_ui_patterns = ["►FIGHT", "PkMn", "ITEM", "RUN", "SCRATCH", "GROWL", "EMBER", "LEER"]
            pattern_matches = sum(1 for pattern in battle_ui_patterns if pattern in dialog)
            # Require multiple indicators to confirm battle UI
            if pattern_matches >= 3:
                logger.info(f"[Battle Detection] Battle UI detected with {pattern_matches} UI elements")
                self.currently_in_battle = True
                return True
                
            # Fallback to keyword detection with explicit phrases
            battle_keywords = [
                "wants to fight", "sent out", "trainer", "battle", "attack",
                "used", "fainted", "effective", "damage", "defeated", "pokémon", "pokemon"
            ]
            
            # Check for explicit battle keywords in dialog
            dialog_lower = dialog.lower()
            for keyword in battle_keywords:
                if keyword in dialog_lower:
                    logger.info(f"[Battle Detection] Battle keyword detected: {keyword}")
                    self.currently_in_battle = True
                    return True
        else:
            # Error - should always either be in a dialog or not in a dialog
            self.currently_in_battle = False
            return False
            
    def navigate_to_move(self, current_index, target_index):
        """Execute precise cursor movement with complete state rendering"""
        logger.info(f"[BattleAI] Cursor at position {current_index}, navigating to target position {target_index}")
        
        # Determine navigation path
        steps = target_index - current_index
        
        if steps == 0:
            return  # Already at target position
        
        # Execute movement with frame rendering
        button = "down" if steps > 0 else "up"
        for i in range(abs(steps)):
            # Press directional button
            current_position = current_index + (i if steps > 0 else -i)
            target_position = current_position + (1 if steps > 0 else -1)
            logger.info(f"[BattleAI] Moving cursor {button.upper()} from position {current_position} to {target_position}")
            
            # Execute button press with complete state synchronization
            self.emulator.press_buttons([button], False)  # Set wait=False for controlled timing
            self.emulator.tick(5)  # Short tick for button registration
            self.emulator.step()   # CRITICAL: Complete full emulator step
            
            # Ensure frame rendering completes
            time.sleep(0.3)
    
    def update_npc_interaction_reward(self, npc_id, map_id):
        """Track NPC interactions and calculate penalties."""
        # npc_id is expected to be sprite_index, map_id is map_id
        # We need to find the NPC's current global coordinates if they are still on screen
        # This is a simplified approach; a more robust method would store NPC global coords when first seen or interacted with.

        # Attempt to find the NPC's global coordinates if they are currently visible
        npc_global_coords = None
        collision_data = self.emulator.get_collision_map()
        if collision_data and collision_data.get("sprite_data"):
            for sprite_info in collision_data["sprite_data"]:
                if sprite_info.get("map_id") == map_id and sprite_info.get("sprite_index") == npc_id:
                    # Found the NPC, get their global coordinates
                    local_x = sprite_info.get("x")
                    local_y = sprite_info.get("y")
                    current_map_id = self.emulator.reader.read_current_map_id() # Ensure correct map ID
                    if local_x is not None and local_y is not None and current_map_id is not None:
                         # Use emulator's local_to_global for consistency
                        g_y, g_x = self.emulator.local_to_global(local_y, local_x, current_map_id)
                        npc_global_coords = (g_y, g_x)
                        logger.debug(f"Found NPC {npc_id} at local ({local_x}, {local_y}) on map {current_map_id}, global ({g_y}, {g_x}).")
                        break # Found the NPC, no need to continue loop

        if not npc_global_coords:
             logger.warning(f"Could not find global coordinates for NPC (map_id: {map_id}, sprite_index: {npc_id}). Cannot update interacted_npcs set.")
             return 0.0 # Cannot track interaction if global coords are unknown

        # Use global coordinates as the key for the interacted_npcs set
        npc_key = npc_global_coords

        # First interaction is neutral (or potentially a small reward if desired)
        if npc_key not in self.interacted_npcs:
            self.interacted_npcs.add(npc_key)
            # You might want a small positive reward for a *first* interaction
            # For now, keep it neutral as per previous logic base
            logger.info(f"Marked NPC at global coords {npc_key} as interacted.")
            return 0.0

        # Additional interactions incur penalty
        logger.info(f"Penalty for interacting with already interacted NPC at global coords {npc_key}.")
        return -1.0  # Penalty for talking to same NPC again
            
    def process_tool_call(self, tool_call):
        """Process a single tool call with reward tracking."""
        tool_name = tool_call.name
        tool_input = tool_call.input
        
        # Record this tool use for tracking
        self.completed_steps.append(tool_name)
        logger.info(f"Processing tool call: {tool_name}")
        
        step_reward = 0.0

        if tool_name == "press_buttons":
            buttons = tool_input["buttons"]
            wait = tool_input.get("wait", True)
            logger.info(f"[Buttons] Pressing: {buttons} (wait={wait})")
            
            # Process button press
            result = self.emulator.press_buttons(buttons, wait)
            
            # Get position after action and calculate reward
            current_position = self.emulator.get_game_coords()
            step_reward = self.calculate_step_reward(current_position)
            
            # Update episode reward
            self.current_episode_reward += step_reward
            self.episode_step += 1
            
            # Check for NPC interactions if action was 'a'
            if 'a' in buttons:
                # Check if we're facing an NPC
                npc_interaction = self.detect_npc_interaction()
                if npc_interaction:
                    npc_id, map_id = npc_interaction
                    npc_reward = self.update_npc_interaction_reward(npc_id, map_id)
                    step_reward += npc_reward
                    self.current_episode_reward += npc_reward
                    
            # Detect map change and record movement log
            new_map = self.emulator.get_location()
            if self.current_map is None:
                self.current_map = new_map
            elif new_map != self.current_map:
                # Save the sequence of movements and clear log
                self.map_stack.append((self.current_map, self.move_log.copy()))
                self.move_log.clear()
                self.current_map = new_map
            
            # Get game state from memory after the action
            memory_info = self.emulator.get_state_from_memory()
            
            # Add reward information to memory info
            reward_info = f"\nREWARD INFORMATION:\n"
            reward_info += f"Step Reward: {step_reward:.2f}\n"
            reward_info += f"Episode Reward: {self.current_episode_reward:.2f}\n"
            reward_info += f"Episode Step: {self.episode_step}/30\n"
            reward_info += f"Total Episodes: {self.episode_count}\n"
            reward_info += f"Total Unique Tiles: {len(self.visited_tiles)}\n"
            if self.episode_rewards:
                reward_info += f"Previous Episode Rewards: {', '.join([f'{r:.2f}' for r in self.episode_rewards])}\n"
            
            # Add dialog/menu state information
            if self.is_in_non_navigation_state():
                reward_info += f"⚠️ DIALOG/MENU ACTIVE - No penalties applied\n"
                # reward_info += f"Use exit_menu tool to clear dialogs/menus\n"
                
                # Check if it might be a battle (persistent dialog that doesn't clear)
                if self.currently_in_battle:
                    reward_info += f"⚠️ CURRENTLY IN BATTLE!\n"
            
            # Combine memory info with reward info
            if memory_info:
                memory_info += reward_info
            else:
                memory_info = reward_info
            
            # Log the memory state after the tool call
            logger.info(f"[Memory State after action]")
            logger.info(memory_info)
            
            # Return tool result with reward information - highlight reward prominently
            return {
                "type": "tool_result",
                "id": tool_call.id,
                "tool_use_id": tool_call.id,
                "content": [
                    {"type": "text", "text": f"REWARD UPDATE: Step Reward: {step_reward:.2f} | Total: {self.current_episode_reward:.2f} | Step {self.episode_step}/30"},
                    {"type": "text", "text": f"\nPressed buttons: {', '.join(buttons)}"},
                    {"type": "text", "text": f"\nGame state information from memory after your action:\n{memory_info}"}
                ],
            }
            
        elif tool_name == "navigate_to":
            glob_y = tool_input["glob_y"]
            glob_x = tool_input["glob_x"]
            logger.info(f"[Navigation] Navigating to global coordinates (Y, X): ({glob_y}, {glob_x})")
            
            status, path = self.emulator.find_path(glob_y, glob_x)
            path_rewards = 0.0
            
            if path is not None:
                # Process each step in the path with rewards
                for direction in path:
                    self.emulator.press_buttons([direction], True)
                    
                    # Calculate reward for this step
                    current_position = self.emulator.get_game_coords()
                    step_reward = self.calculate_step_reward(current_position)
                    path_rewards += step_reward
                    
                    # Update episode tracking
                    self.current_episode_reward += step_reward
                    self.episode_step += 1
                    
                    # Check for episode end
                    if self.episode_step >= 30:
                        break
                
                result = f"Navigation successful: followed path with {len(path)} steps, total reward: {path_rewards:.2f}"
            else:
                result = f"Navigation failed: {status}"
            
            # Get game state from memory after the action
            memory_info = self.emulator.get_state_from_memory()
            
            # Add reward information
            reward_info = f"\nREWARD INFORMATION:\n"
            reward_info += f"Path Reward: {path_rewards:.2f}\n"
            reward_info += f"Episode Reward: {self.current_episode_reward:.2f}\n"
            reward_info += f"Episode Step: {self.episode_step}/30\n"
            reward_info += f"Total Episodes: {self.episode_count}\n"
            reward_info += f"Total Unique Tiles: {len(self.visited_tiles)}\n"
            if self.episode_rewards:
                reward_info += f"Previous Episode Rewards: {', '.join([f'{r:.2f}' for r in self.episode_rewards])}\n"
            
            # Add dialog/menu state information
            if self.is_in_non_navigation_state():
                reward_info += f"⚠️ DIALOG/MENU ACTIVE - No penalties applied\n"
                # reward_info += f"Use exit_menu tool to clear dialogs/menus\n"
                
                # Check if it might be a battle (persistent dialog that doesn't clear)
                if self.currently_in_battle:
                    reward_info += f"⚠️ CURRENTLY IN BATTLE!\n"
            
            # Combine memory info with reward info
            if memory_info:
                memory_info += reward_info
            else:
                memory_info = reward_info
            
            # Return tool result with reward information - highlight reward prominently
            return {
                "type": "tool_result",
                "id": tool_call.id,
                "tool_use_id": tool_call.id,
                "content": [
                    {"type": "text", "text": f"REWARD UPDATE: Path Reward: {path_rewards:.2f} | Total: {self.current_episode_reward:.2f} | Step {self.episode_step}/30"},
                    {"type": "text", "text": f"\nNavigation attempt to global ({glob_y}, {glob_x}): {result}"},
                    {"type": "text", "text": f"\nGame state information from memory after your action:\n{memory_info}"},
                ],
            }
            
        # elif tool_name == "exit_menu":
        #     logger.info("[Menu] Attempting to exit menu")
        #     result = exit_menu(self.emulator)

        #     # Get game state from memory after the action
        #     memory_info = self.emulator.get_state_from_memory()

        #     # Add reward information
        #     reward_info = f"\nREWARD INFORMATION:\n"
        #     reward_info += f"Step Reward: {step_reward:.2f}\n"
        #     reward_info += f"Episode Reward: {self.current_episode_reward:.2f}\n"
        #     reward_info += f"Episode Step: {self.episode_step}/30\n"
        #     reward_info += f"Total Episodes: {self.episode_count}\n"
        #     reward_info += f"Total Unique Tiles: {len(self.visited_tiles)}\n"

        #     # Add dialog/menu state information
        #     if self.is_in_non_navigation_state():
        #         reward_info += f"⚠️ DIALOG/MENU ACTIVE - No penalties applied\n"
        #         # reward_info += f"Use exit_menu tool to clear dialogs/menus\n"

        #         # Check if it might be a battle (persistent dialog that doesn't clear)
        #         if self.currently_in_battle:  
        #             reward_info += f"⚠️ CURRENTLY IN BATTLE!\n"

        #     # Combine memory info with reward info
        #     if memory_info:
        #         memory_info += reward_info
        #     else:
        #         memory_info = reward_info

        #     # Return tool result with reward information
        #     return {
        #         "type": "tool_result",
        #         "id": tool_call.id,
        #         "tool_use_id": tool_call.id,
        #         "content": [
        #             {"type": "text", "text": f"Attempted to exit menu."},
        #             {"type": "text", "text": f"\nGame state information from memory after your action:\n{memory_info}"},
        #         ],
        #     }
            
        # elif tool_name == "handle_battle":
        #     # PROTOCOL: Synchronized battle execution sequence
            
        #     # 1. CAPTURE INITIAL STATE
        #     dialog_before = self.emulator.get_active_dialog() or ""
        #     status_text = dialog_before.strip().replace("\n", " ") if dialog_before else "(Battle dialog cleared)"
            
        #     # 2. SYNCHRONIZATION PHASE
        #     # Execute full emulator step for state stabilization
        #     self.emulator.step()
        #     time.sleep(0.5)  # Extended stabilization delay
            
        #     # 3. BATTLE MENU VERIFICATION
        #     battle_menu_visible = "►FIGHT" in dialog_before
        #     logger.info(f"[BattleAI] Battle menu state: {'Active' if battle_menu_visible else 'Not detected'}")
            
        #     if not battle_menu_visible:
        #         # Reset to known UI state
        #         logger.info("[BattleAI] Battle menu not detected, attempting to reset UI state")
        #         self.emulator.press_buttons(["b"], True)
        #         self.emulator.step()
        #         time.sleep(0.3)
            
        #     # 4. FIGHT MENU SELECTION
        #     logger.info("[BattleAI] Selecting FIGHT menu option")
        #     self.emulator.press_buttons(["a"], True)
            
        #     # Complete full emulator step cycle
        #     self.emulator.step()
        #     time.sleep(0.5)  # Extended stabilization delay
            
        #     # 5. MOVE DETERMINATION
        #     try:
        #         best_idx = self.choose_best_battle_move()
        #         logger.info(f"[BattleAI] Best move determined: index {best_idx}")
        #     except Exception as e:
        #         logger.error(f"[BattleAI] Error determining best move: {e}", exc_info=True)
        #         best_idx = 2  # Default to EMBER (position 2) for bug-type enemies
        #         logger.warning(f"[BattleAI] Defaulting to EMBER (index 2) after calculation error")
            
        #     # 6. CURSOR NAVIGATION
        #     # Establish precise cursor position
        #     logger.info(f"[BattleAI] Navigation to target position {best_idx}")

        #     # RESET PHASE: Ensure cursor begins at known position 0
        #     for _ in range(4):  # Comprehensive reset sequence
        #         self.emulator.press_buttons(["up"], False)
        #         self.emulator.tick(5)
        #         self.emulator.step()
        #         time.sleep(0.1)  # Stabilization interval

        #     # VERIFICATION DIAGNOSTIC: Record starting position
        #     logger.info(f"[BattleAI] Cursor reset complete, beginning at position 0")

        #     # NAVIGATION PHASE: Execute precisely (best_idx) movements
        #     if best_idx > 0:  # Only navigate if not already at target
        #         for i in range(best_idx):
        #             logger.info(f"[BattleAI] Moving cursor {i} → {i+1}")
        #             self.emulator.press_buttons(["down"], False)
        #             self.emulator.tick(10)  # Extended timing for reliable registration
        #             self.emulator.step()
        #             time.sleep(0.4)  # Extended stabilization interval

        #     # POSITION VERIFICATION: Confirm final position
        #     logger.info(f"[BattleAI] Navigation complete. Target: {best_idx}, Movements: {best_idx}")
            
        #     # 7. MOVE EXECUTION
        #     logger.info(f"[BattleAI] Selecting move at position {best_idx}")
        #     self.emulator.press_buttons(["a"], True)
            
        #     # Complete full emulator step with extended delay
        #     self.emulator.step()
        #     time.sleep(0.3)  # Allow battle dialog to appear
            
        #     # 8. STATE UPDATE
        #     self.episode_step += 1
        #     move_name = self.last_chosen_move_name or f"Move at position {best_idx}"
        #     self.last_message = f"Battle: Selected {move_name} against {status_text}"
            
        #     # 9. RESPONSE GENERATION
        #     return {
        #         "type": "tool_result",
        #         "id": tool_call.id,
        #         "tool_use_id": tool_call.id,
        #         "content": [
        #             {"type": "text", "text": f"Battle Text: \"{status_text}\""},
        #             {"type": "text", "text": f"Selected move: {move_name} (position {best_idx})"},
        #             {"type": "text", "text": f"Battle action completed with full frame rendering at each step."}
        #         ],
            # }
            
    def detect_npc_interaction(self):
        """Detect if the player is interacting with an NPC.
        Returns tuple of (map_id, sprite_index) if interacting, None otherwise.
        """
        logger = logging.getLogger(__name__)
        # Check for dialog - if no dialog, no interaction is happening
        dialog = self.emulator.get_active_dialog()
        if not dialog:
            # logger.debug("detect_npc_interaction: No dialog active.") # Suppress this frequent debug log
            return None
            
        # Get player position and facing direction on the 9x10 grid
        collision_data = self.emulator.get_collision_map()
        player_grid_pos = collision_data.get("grid_position")
        player_facing = collision_data.get("player_position", {}).get("direction")
        sprite_data_list = collision_data.get("sprite_data", [])
        current_map_id = self.emulator.reader.read_current_map_id()

        if not player_grid_pos or not player_facing or not sprite_data_list:
            logger.debug(f"detect_npc_interaction: Missing data or no sprites - player_grid_pos: {player_grid_pos}, player_facing: {player_facing}, sprite_data_list count: {len(sprite_data_list)}")
            return None # Not enough data to determine interaction
            
        pr, pc = player_grid_pos["row"], player_grid_pos["col"]
        # logger.debug(f"detect_npc_interaction: Player at grid ({pr}, {pc}), facing {player_facing}") # Suppress this frequent debug log
        
        # Determine the grid cell the player is facing
        facing_row, facing_col = pr, pc
        if player_facing == "up":
            facing_row -= 1
        elif player_facing == "down":
            facing_row += 1
        elif player_facing == "left":
            facing_col -= 1
        elif player_facing == "right":
            facing_col += 1
        logger.debug(f"detect_npc_interaction: Player facing grid cell ({facing_row}, {facing_col})")
        
        # Check if the faced cell contains an NPC sprite
        for npc_info in sprite_data_list:
            # logger.debug(f"detect_npc_interaction: Checking sprite at grid ({npc_info.get('grid_row')}, {npc_info.get('grid_col')}) with index {npc_info.get('sprite_index')} on map {npc_info.get('map_id')}") # Suppress this frequent debug log
            if npc_info["grid_row"] == facing_row and npc_info["grid_col"] == facing_col:
                # Found an NPC in the faced cell
                # Use map_id and sprite_index as the unique key
                logger.info(f"detect_npc_interaction: Found NPC at faced cell ({facing_row}, {facing_col}). Key: ({current_map_id}, {npc_info['sprite_index']})")
                return (current_map_id, npc_info["sprite_index"])

        # logger.debug("detect_npc_interaction: No NPC found in faced cell.") # Suppress this frequent debug log
        return None # No NPC detected in the faced cell
    
    def step(self, force_render=False):
        """Execute a single step with strict timing controls and navigation."""
        
        current_time = time.time()
        if self.event_tracking_enabled and current_time - self.last_event_check_time >= self.event_check_interval:
            self.update_event_tracking()
            self.last_event_check_time = current_time
        
        # Log the start of step execution
        logger.info("Starting step execution")
        
        # Check for any active dialog and current battle state
        dialog = self.emulator.get_active_dialog()
        in_battle = self.might_be_battle()
        
        # CRITICAL: maintain battle state across the entire encounter
        # If we were in battle previously or critical hit appears in dialog, we're still in battle
        if (self.was_in_battle or 
            (dialog and any(x in dialog.lower() for x in ["critical hit", "super effective", "not very effective", "used", "fainted"]))):
            in_battle = True
            logger.info("Continuing battle state based on previous state or battle message")
        
        # Check if a battle just finished
        battle_just_finished = self.was_in_battle and not in_battle
        
        # Log dialog/battle state
        if dialog:
            logger.info(f"Dialog detected: {dialog[:30]}...")
            if in_battle:
                logger.info("Battle state detected")
            else:
                logger.info("Dialog state detected")
        else:
            logger.info("No dialog detected")
        
        # CRITICAL SYNCHRONIZATION UPDATE: Force emulator step for UI stability
        if force_render or in_battle:
            self.emulator.step()
            time.sleep(0.1)  # Stabilization delay

        # If any dialog or battle is active, defer all decisions to LLM
        if dialog:
            dialog_text = dialog.replace('\n', ' ')[:500]
            logger.info(f"Dialog or battle active: {dialog_text}")
            self.last_message = f"Dialog: {dialog_text}"
        # Always ask the LLM to choose the next action via tools
        logger.info("Requesting next action from LLM via OpenAI endpoint")

        try:
            # Add the current game state (including dialog, battle status, rewards) for LLM context
            try:
                state_text = self.format_game_state()
                self.message_history.append({"role": "user", "content": [{"type": "text", "text": state_text}]})
            except Exception as e:
                logger.error(f"Failed to append game state to message history: {e}", exc_info=True)
            # Sanitize history to remove duplicates and enforce structure
            self.sanitize_message_history()
            # Initialize LLM client
            client_kwargs = {"api_key": self.xai_api_key}
            if getattr(self, 'llm_provider', None) == 'grok':
                client_kwargs["base_url"] = os.getenv("XAI_API_BASE", "https://api.x.ai/v1")
            # Restrict tools based on battle or dialog state
            tools_to_use = self.xai_tools
            if in_battle:
                tools_to_use = [t for t in self.xai_tools if t["function"]["name"] == "press_buttons"]
            # elif dialog:
            #     tools_to_use = [t for t in self.xai_tools if t["function"]["name"] == "exit_menu"]
            # Log the LLM request messages for debugging
            try:
                log_msg = json.dumps(self.message_history, indent=2)
                logger.info("LLM request messages:\n%s", log_msg)
                # Also log to game.log in run directory
                if hasattr(self, 'app') and getattr(self.app.state, 'grok_logger', None):
                    self.app.state.grok_logger.info("LLM request messages:\n%s", log_msg)
            except Exception as log_e:
                logger.error(f"Failed to log LLM request messages: {log_e}")
            client = OpenAI(**client_kwargs)
            # Request completion with tools
            completion = client.chat.completions.create(
                model            = self.model_name,
                reasoning_effort = "low",
                tools            = tools_to_use,
                tool_choice      = "auto",
                messages         = self.message_history,
                temperature      = self.temperature,
                max_tokens       = self.max_tokens,
            )
            # Log LLM raw response
            try:
                msg = completion.choices[0].message
                resp_content = msg.content or ""
                logger.info("LLM response content: %s", resp_content)
                if hasattr(self, 'app') and getattr(self.app.state, 'grok_logger', None):
                    self.app.state.grok_logger.info("LLM response content: %s", resp_content)
                # Log any tool calls suggested by LLM
                raw_tool_calls = msg.tool_calls or []
                logger.info("LLM tool_calls: %s", [(tc.function.name, tc.function.arguments) for tc in raw_tool_calls])
                if hasattr(self, 'app') and getattr(self.app.state, 'grok_logger', None):
                    self.app.state.grok_logger.info("LLM tool_calls: %s", [(tc.function.name, tc.function.arguments) for tc in raw_tool_calls])
            except Exception as log_e:
                logger.error(f"Failed to log LLM response or tool_calls: {log_e}")
            # Extract tool calls and process each
            tool_calls = msg.tool_calls or []
            for tc in tool_calls:
                func_name = tc.function.name
                func_args = json.loads(tc.function.arguments or "{}")
                result_obj = self.process_tool_call(
                    FakeToolCall(func_name, func_args, tc.id)
                )
                # Log tool call and result
                try:
                    log_entry = f"Tool call '{func_name}' args: {func_args} -> result: {result_obj}"
                    logger.info(log_entry)
                    if hasattr(self, 'app') and getattr(self.app.state, 'grok_logger', None):
                        self.app.state.grok_logger.info(log_entry)
                except Exception as log_e:
                    logger.error(f"Failed to log tool call/result: {log_e}")
                self.message_history.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result_obj),
                })
            # Determine last_message: show invoked tools or assistant content
            if tool_calls:
                tool_details = []
                for tc in tool_calls:
                    func_name = tc.function.name
                    func_args = json.loads(tc.function.arguments or "{}")
                    tool_details.append(f"{func_name}({func_args})")
                # Name the specific tool call invoked, and its result e.g. "Pressed buttons: a"    
                self.last_message = f"Grok invoked tool call {tool_calls}: {', '.join(tool_details)}"
            else:
                content = msg.content or ""
                self.last_message = content.strip()

        except Exception as e:
            logger.error(f"LLM action selection failed: {e}", exc_info=True)

        # Update reward state
        try:
            if hasattr(self, 'update_reward_state'):
                self.update_reward_state()
                
            if hasattr(self, 'update_map_progress'):
                self.update_map_progress()
                
            if hasattr(self, 'update_seen_npcs'):
                self.update_seen_npcs()
                
            if hasattr(self, 'update_seen_coords'):
                self.update_seen_coords()
        except Exception as e:
            logger.error(f"Error updating state tracking: {e}", exc_info=True)
                
        # Get current coordinates after action
        current_location = self.emulator.get_game_coords()
        
        # Update stuck detection trackers
        if hasattr(self, 'last_location'):
            if self.last_location == current_location:
                self.persistent_location_count = getattr(self, 'persistent_location_count', 0) + 1
                logger.info(f"Same location detected {self.persistent_location_count} times: {current_location}")
            else:
                self.persistent_location_count = 0
                logger.info(f"Location changed to {current_location}")
        
        self.last_location = current_location
        
        # Update battle tracking for the next step
        self.was_in_battle = in_battle
        
        # # CRITICAL: Enforce minimum 3-second delay after any action
        # logger.info("Enforcing 3-second delay between actions")
        # time.sleep(3.0)
        
        # CRITICAL SYNCHRONIZATION UPDATE: Final frame stabilization
        if force_render or in_battle:
            self.emulator.step()
        
        logger.info("Step execution completed")
    
    
    # Helper function to compare frames
    def numpy_frame_equal(frame1, frame2):
        """Compare two PIL Image frames to see if they're the same."""
        try:
            import numpy as np
            # Convert PIL images to numpy arrays
            arr1 = np.array(frame1)
            arr2 = np.array(frame2)
            # Check if arrays are the same shape
            if arr1.shape != arr2.shape:
                return False
            # Check if all pixels are the same
            return np.array_equal(arr1, arr2)
        except Exception as e:
            logger.error(f"Error comparing frames: {e}")
            # Fallback comparison if numpy not available
            return frame1 == frame2
        
    def choose_best_battle_move(self):
        """
        Determine optimal move based on type effectiveness against enemy Pokémon.
        Returns the index of most effective move (0-3).
        """
        logger.info("choose_best_battle_move aka [BattleAI] called.")
        
        reader = self.emulator.reader

        # 1. Get enemy Pokémon types
        try:
            primary_type, secondary_type = reader.read_enemy_current_pokemon_types()
            type_id_primary = primary_type.value if isinstance(primary_type, PokemonType) else primary_type
            logger.info(f"[BattleAI] Enemy types: Primary={type_id_primary}, Secondary={secondary_type.value if secondary_type else None}")
            
            # Verify enemy is Bug type (7) for Caterpie
            if type_id_primary != PokemonType.BUG.value:
                logger.warning(f"[BattleAI] Expected Bug type (7) for Caterpie, got {type_id_primary}")
        except Exception as e:
            logger.error(f"[BattleAI] Failed to read enemy types: {e}")
            return 0  # Default to first move
        
        # 2. Get current active Pokemon's move information from party
        try:
            party = reader.read_party_pokemon()
            if not party:
                logger.error("[BattleAI] Failed to read party data")
                return 0
                
            active_pokemon = party[0]  # First Pokemon in party is active
            move_names = active_pokemon.moves
            logger.info(f"[BattleAI] Active Pokemon: {active_pokemon.species_name}, Moves: {move_names}")
            
            # Pre-defined move types for Charmander's starting moves
            # Hard-coded for stability since memory reading has issues
            move_types = {
                "SCRATCH": PokemonType.NORMAL,  # Normal type
                "GROWL": PokemonType.NORMAL,    # Normal type
                "EMBER": PokemonType.FIRE,      # Fire type
                "LEER": PokemonType.NORMAL      # Normal type
            }
            
            # Pre-defined move power for stability
            move_power = {
                "SCRATCH": 40,
                "GROWL": 0,    # Status move
                "EMBER": 40,   # Base power (actually higher vs Bug)
                "LEER": 0      # Status move
            }
        except Exception as e:
            logger.error(f"[BattleAI] Failed to initialize move data: {e}")
            return 0
        
        # 3. Initialize structures for move evaluation
        best_index = 0
        best_effective_power = -1.0
        move_details = []
        
        # 4. Reset cursor position (navigate to top of move list)
        for i in range(4):
            self.emulator.press_buttons(["up"], wait=False)
            self.emulator.tick(5)
            logger.info(f"[BattleAI] Reset cursor iteration {i+1}/4")
        
        # 5. Evaluate each move
        for idx in range(len(move_names)):
            # Move cursor down if needed
            if idx > 0:
                self.emulator.press_buttons(["down"], wait=False)
                self.emulator.tick(5)
                logger.info(f"[BattleAI] Moved cursor down to index {idx}")
            
            move_name = move_names[idx]
            
            # Determine move type and power using predefined data
            move_type = move_types.get(move_name, PokemonType.NORMAL)
            base_power = move_power.get(move_name, 0)
            
            # Skip evaluating damage for status moves
            if base_power == 0:
                logger.info(f"[BattleAI] Move {idx} ({move_name}): Status move, skipping damage calculation")
                move_details.append({
                    "index": idx,
                    "name": move_name,
                    "base_power": 0,
                    "type": move_type,
                    "multiplier": 0,
                    "effective_power": 0
                })
                continue
            
            # Calculate type effectiveness
            multiplier = 1.0
            if move_type == PokemonType.FIRE and type_id_primary == PokemonType.BUG.value:
                multiplier = 2.0  # Fire is super effective against Bug
                logger.info(f"[BattleAI] Type matchup: FIRE vs BUG = 2.00x (super effective)")
            else:
                logger.info(f"[BattleAI] Type matchup: {move_type.name} vs {PokemonType(type_id_primary).name} = {multiplier:.2f}x")
            
            # Calculate effective power
            effective_power = base_power * multiplier
            
            # Log move evaluation
            logger.info(f"[BattleAI] Evaluated move {idx} ({move_name}): base={base_power}, multiplier={multiplier:.2f}, effective_power={effective_power:.2f}")
            
            # Track move details
            move_details.append({
                "index": idx,
                "name": move_name,
                "base_power": base_power,
                "type": move_type,
                "multiplier": multiplier,
                "effective_power": effective_power
            })
            
            # Update best move if this one is more effective
            if effective_power > best_effective_power:
                best_effective_power = effective_power
                best_index = idx
        
        # 6. Log evaluation summary
        logger.info("[BattleAI] Move evaluation summary:")
        for move in move_details:
            type_name = move["type"].name if hasattr(move["type"], "name") else str(move["type"])
            logger.info(f"  - {move['index']}: {move['name']} ({move['base_power']} power, {type_name} type) = {move['effective_power']:.2f} effective power")
        
        logger.info(f"[BattleAI] Selected best move: {best_index} ({move_details[best_index]['name']}) with {best_effective_power:.2f} effective power")
        
        # 7. Store move selection for UI display
        self.last_chosen_move_index = best_index
        self.last_chosen_move_name = move_details[best_index]['name']
        self.last_move_list = move_names
        
        return best_index
    
    def summarize_history(self):
        """Generate a summary focused on exploration rewards with improved state formatting."""
        # Create a summary focused entirely on reward optimization
        episode_summary = f"Exploration Summary:\n"
        episode_summary += f"Episodes completed: {self.episode_count}\n"
        if self.episode_rewards:
            episode_summary += f"Previous episode rewards: {', '.join([f'{r:.2f}' for r in self.episode_rewards])}\n"
            episode_summary += f"Best episode reward: {max(self.episode_rewards):.2f}\n"
            episode_summary += f"Average episode reward: {sum(self.episode_rewards)/len(self.episode_rewards):.2f}\n"
        
        episode_summary += f"Current episode progress: {self.episode_step}/30 steps, {self.current_episode_reward:.2f} reward\n"
        episode_summary += f"Unique tiles explored: {len(self.visited_tiles)}\n"
        episode_summary += f"NPC interactions: {len(self.interacted_npcs)}\n"
        
        # Strategy reminder
        episode_summary += "\nREMINDER: Your goal is to maximize exploration rewards:\n"
        episode_summary += "- +0.01 for each new tile visited\n"
        episode_summary += "- -0.02 penalty for revisiting tiles\n"
        episode_summary += "- -1.00 penalty for talking to the same NPC multiple times\n"
        episode_summary += "Episodes reset every 30 steps.\n"
        
        # Replace message history with just the system prompt and summary
        self.message_history = [
            {
                "role": "system", 
                "content": self.system_prompt
            },
            {
                "role": "user",
                "content": f"EXPLORATION REWARD SUMMARY:\n{episode_summary}\n\nHelp me maximize exploration rewards in the next steps."
            }
        ]
        
        # Update game state after summary
        self.latest_game_state = self.emulator.get_state_from_memory()
        
        # Add reward information
        reward_info = f"\nREWARD INFORMATION:\n"
        reward_info += f"Current Episode: {self.episode_count + 1}\n"
        reward_info += f"Episode Step: {self.episode_step}/30\n"
        reward_info += f"Episode Reward So Far: {self.current_episode_reward:.2f}\n"
        reward_info += f"Total Unique Tiles: {len(self.visited_tiles)}\n"
        if self.episode_rewards:
            reward_info += f"Previous Episode Rewards: {', '.join([f'{r:.2f}' for r in self.episode_rewards])}\n"
        
        self.latest_game_state += reward_info
        
        # Add current game state as a user message with clear formatting
        formatted_state = self.format_game_state()
        self.message_history.append(
            {"role": "user",
            "content": formatted_state}
        )
            
        # Update last message
        self.last_message = f"Generated exploration reward summary"
        
        return episode_summary

    def stop(self):
        """Stop the agent."""
        self.running = False
        self.emulator.stop()

    def get_valid_moves(self) -> list[str]:
        return self.emulator.get_valid_moves()
        
    def get_last_message(self) -> str:
        """Get the agent's most recent message.
        
        Returns:
            str: The last message from the agent, or a default message if none exists
        """
        return self.last_message if hasattr(self, 'last_message') else "No message available"
        
    def get_frame(self) -> bytes:
        """Get the current game frame as PNG bytes.
        
        Returns:
            bytes: PNG-encoded screenshot of the current frame
        """
        screenshot = self.emulator.get_screenshot()
        # Convert PIL image to PNG bytes
        buffered = io.BytesIO()
        screenshot.save(buffered, format="PNG")
        return buffered.getvalue()
        
    def get_party(self) -> list:
        """Get the player's Pokémon party information.
        
        Returns:
            list: List of dictionaries containing Pokémon data
        """
        try:
            party_data = self.emulator.reader.read_party_pokemon()
            # Convert to simple dict format for JSON serialization
            party = []
            
            for pokemon in party_data:
                party.append({
                    'species': pokemon.species_name,
                    'nickname': pokemon.nickname,
                    'level': pokemon.level,
                    'hp': pokemon.current_hp,
                    'max_hp': pokemon.max_hp,
                    'status': pokemon.status_name,
                    'type1': pokemon.type1.name if pokemon.type1 else None,
                    'type2': pokemon.type2.name if pokemon.type2 else None,
                    'moves': pokemon.moves
                })
            
            return party
        except Exception as e:
            logger.error(f"Error getting party data: {e}")
            return []

    # Event monitoring integration
    def monitor_game_events(self):
        """
        Call event monitoring functions from ram_map_leanke.py
        Returns:
            dict: Structured event data by area
        """
        memory_adapter = MemoryAdapter(self.emulator)
        events = {
            "GYMS": {
                "GYM1": ram_map.gym1(memory_adapter),
                "GYM2": ram_map.gym2(memory_adapter),
                "GYM3": ram_map.gym3(memory_adapter),
                "GYM4": ram_map.gym4(memory_adapter),
                "GYM5": ram_map.gym5(memory_adapter),
                "GYM6": ram_map.gym6(memory_adapter),
                "GYM7": ram_map.gym7(memory_adapter),
                "GYM8": ram_map.gym8(memory_adapter)
            },
            "AREAS": {
                "SILPH_CO": ram_map.silph_co(memory_adapter),
                "ROCK_TUNNEL": ram_map.rock_tunnel(memory_adapter),
                "MTMOON": ram_map.mtmoon(memory_adapter),
                "POKEMON_TOWER": ram_map.poke_tower(memory_adapter),
                "ROCKET_HIDEOUT": ram_map.hideout(memory_adapter),
                "MANSION": ram_map.mansion(memory_adapter),
                "SAFARI": ram_map.safari(memory_adapter),
                "DOJO": ram_map.dojo(memory_adapter)
            },
            "EVENTS": {
                "SNORLAX": ram_map.snorlax(memory_adapter),
                "HMTM": ram_map.hmtm(memory_adapter),
                "BILL": ram_map.bill(memory_adapter),
                "OAK": ram_map.oak(memory_adapter),
                "TOWNS": ram_map.towns(memory_adapter),
                "LAB": ram_map.lab(memory_adapter),
                "MISC": ram_map.misc(memory_adapter)
            }
        }
        detailed_events = {
            "SILPH_CO": ram_map.monitor_silph_co_events(memory_adapter),
            "ROCK_TUNNEL": ram_map.rock_tunnel_events(memory_adapter),
            "GYM3": ram_map.monitor_gym3_events(memory_adapter),
            "GYM4": ram_map.monitor_gym4_events(memory_adapter),
            "GYM5": ram_map.monitor_gym5_events(memory_adapter),
            "GYM6": ram_map.monitor_gym6_events(memory_adapter),
            "GYM7": ram_map.monitor_gym7_events(memory_adapter),
            "GYM8": ram_map.monitor_gym8_events(memory_adapter),
            "DOJO": ram_map.monitor_dojo_events(memory_adapter),
            "HIDEOUT": ram_map.monitor_hideout_events(memory_adapter),
            "POKE_TOWER": ram_map.monitor_poke_tower_events(memory_adapter),
            "LAB": ram_map.monitor_lab_events(memory_adapter),
            "MANSION": ram_map.monitor_mansion_events(memory_adapter),
            "SAFARI": ram_map.monitor_safari_events(memory_adapter),
            "SNORLAX": ram_map.monitor_snorlax_events(memory_adapter),
            "HMTM": ram_map.monitor_hmtm_events(memory_adapter)
        }
        return {"summary": events, "detailed": detailed_events}

    def analyze_game_progression(self):
        """
        Process event data and generate recommendations.
        """
        events = self.monitor_game_events()
        current_location = self.emulator.reader.read_location()
        # Include current map ID for context
        current_map_id = self.emulator.reader.read_current_map_id()
        badges = self.emulator.reader.read_badges()
        progress_stats = self._calculate_progress_stats(events, badges)
        relevant_areas = self._get_relevant_areas(current_location)
        recommendations = self._generate_recommendations(events, badges)
        # Format progression for event summary
        progression = {
            "current_location": current_location,
            "map_id": current_map_id,
            "badges": badges,
            "recommended_next_steps": recommendations,
            "major_areas": relevant_areas
        }
        return progression

    def _calculate_progress_stats(self, events, badges):
        """Calculate game progress statistics."""
        # Implementation details...
        return {}

    def _get_relevant_areas(self, current_location):
        """Determine which areas are relevant to current location."""
        # Implementation details...
        return {}

    def _generate_recommendations(self, events, badges):
        """Generate game progression recommendations."""
        # Implementation details...
        return []

    @property
    def currently_in_dialog(self) -> bool:
        """Return True if agent is in a dialog or menu (but not battle)."""
        return self.is_in_non_navigation_state() and not getattr(self, 'currently_in_battle', False)