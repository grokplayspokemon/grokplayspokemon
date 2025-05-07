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
from pathlib import Path

from agent.prompts import SYSTEM_PROMPT, SUMMARY_PROMPT
from agent.tools import AVAILABLE_TOOLS
from agent.emulator import Emulator
from game_data.nav import Nav
try:
    from bin.ram_reader.red_ram_api import *
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

class SimpleAgent:
    def __init__(self, cfg, app=None):
        """Initialize the simple agent focused on exploration rewards."""
        self.emulator = Emulator(cfg.rom_path, cfg.emulator_headless, cfg.emulator_sound)
        self.emulator.initialize()
        self.emulator.register_hooks()
        self.nav = Nav(self.emulator)
        
        # Initialize PyBoyManager with the Emulator instance if needed
        try:
            self.pyboy_manager = PyBoyManager(cfg, emulator=self.emulator)
        except ImportError:
            logger.warning("PyBoyManager could not be imported, proceeding without it.")
            self.pyboy_manager = None
        
        # Set Grok provider settings
        self.model_name = cfg.llm_model
        self.temperature = cfg.llm_temperature
        self.max_tokens = cfg.llm_max_tokens
        
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
        self._last_battled_npc_key: tuple[int, int] | None = None # Track (map_id, sprite_index) of the last NPC battled
        
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
        self.message_history = [{
            "role": "system",
            "content": self._get_system_prompt()
        },
        {
            "role": "user",
            "content": "I am playing a tile exploration game. Help me maximize my rewards."
        }]

        # Initialize tracking
        self.prev_dialog_text = None
        self.completed_steps = []
        self.map_stack = []
        self.move_log = []
        self.current_map = None
        
        cfg.debug = False  # or True, as appropriate
        cfg.extra_buttons = False  # or True, as appropriate
    
    def determine_optimal_tool_choice(self):
        """Determine which tool to force based on current game state."""
        # Check for battles first - highest priority
        if self.might_be_battle() and self.is_in_non_navigation_state():
            # If in battle, prioritize handle_battle
            logger.info("[Tool Selection] Battle detected, forcing handle_battle tool")
            return "handle_battle"    
        # Check if in dialog or menu state - second priority
        elif self.is_in_non_navigation_state():
            # If in dialog/menu, prioritize exit_menu
            return "exit_menu"
        else:   
            # Default to press_buttons as the most commonly used tool
            return "press_buttons"
    
    def sanitize_message_history(self):
        """Filter message history to remove text-only responses that might influence Grok."""
        sanitized_messages = []
        
        for msg in self.message_history:
            # Keep all system and user messages
            if msg["role"] in ["system", "user"]:
                sanitized_messages.append(msg)
                continue
                
            # For assistant messages, only keep those with tool calls
            if msg["role"] == "assistant":
                if "tool_calls" in msg and msg["tool_calls"]:
                    # Message has tool calls, keep it
                    sanitized_messages.append(msg)
                else:
                    # Message is text-only, convert to system message to reduce mimicry
                    sanitized_messages.append({
                        "role": "system",
                        "content": "NOTE: Always use tool calls instead of text responses."
                    })
        
        self.message_history = sanitized_messages
        
    def _get_system_prompt(self) -> str:
        """Get the core system prompt with tool usage instructions."""
        prompt = self.system_prompt
        
        # Add explicit tool usage instructions
        prompt += "\n\nCRITICAL INSTRUCTION: You MUST use the provided tools for all actions. DO NOT suggest actions in text form (e.g., 'press up'). Always use the press_buttons, navigate_to, exit_menu, handle_battle, or exit_to_last_map tools explicitly.\n"
        
        # List available tools with clear usage examples
        prompt += "\nAvailable tools:\n"
        prompt += "1. press_buttons: Use to press specific buttons (e.g., {\"buttons\": [\"up\"], \"wait\": true})\n"
        prompt += "2. navigate_to: Use to move to a specific grid position (e.g., {\"row\": 3, \"col\": 4})\n"
        prompt += "3. exit_menu: Use to exit any active menu or dialog\n"
        prompt += "4. handle_battle: Use to handle battle situations\n"
        prompt += "5. exit_to_last_map: Use to return to previous map\n"
        
        return prompt

    def format_game_state(self) -> str:
        """
        Format the current game state in a way that's optimized for LLM processing.
        Separates state information with clear markers and standardized structure.
        """
        # Start with explicit header
        state_parts = ["## CURRENT GAME STATE ##"]
        
        # Add reward information prominently at the top
        state_parts.append("REWARD STATUS:")
        # Note: Using episode_step instead of step_counter to match existing attribute structure
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
            state_parts.append("Use exit_menu tool to clear dialogs/menus")
            
            # Add battle detection
            if self.might_be_battle():
                state_parts.append("⚠️ POSSIBLE BATTLE DETECTED - Consider using handle_battle tool")
        
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
        if dialog is not None and dialog != "None" and dialog != " None" and dialog != "":
            # Enhanced dialog detection with menu keywords
            menu_indicators = [
                "POKéDEX", "POKéMON", "ITEM", "SAVE", "OPTION", "EXIT",
                "►", "MENU", "PC", "BADGE", "MAP", "TRAINER"
            ]
            
            # Check for multiple menu indicators
            if dialog:
                indicator_count = sum(1 for keyword in menu_indicators if keyword in dialog)
                if indicator_count >= 2:  # If multiple menu keywords detected, definitely in a menu
                    logger.info(f"[Menu Detection] Menu state detected with {indicator_count} indicators")
                    return True
            
            # Standard dialog check
            return True
            
        # Additional checks for menu state:
        # 1. Check if game coords haven't changed despite multiple movement attempts
        # 2. Check if reward is consistently 0.00 or negative despite movement attempts
        if hasattr(self, 'persistent_location_count') and self.persistent_location_count > 3:
            logger.info(f"[Menu Detection] Detected possible menu state from persistent location")
            return True
            
        # For now, just check dialog with enhanced detection
        return False
        
    def might_be_battle(self):
        """Determine if current dialog might be a battle dialog based on content.
        
        This method checks for the battle UI pattern and battle-related terms.
        """
        dialog = self.emulator.get_active_dialog()
        if not dialog:
            self.currently_in_battle = False
            return False
            
        # Primary battle indicator: FIGHT menu option in the battle UI
        if "►FIGHT" in dialog:
            logger.info("[Battle Detection] Battle UI detected with ►FIGHT menu")
            self.currently_in_battle = True
            return True
            
        # Secondary battle UI pattern detection (include move options)
        battle_ui_patterns = ["FIGHT", "PkMn", "ITEM", "RUN", "SCRATCH", "GROWL", "EMBER", "LEER"]
        pattern_matches = sum(1 for pattern in battle_ui_patterns if pattern in dialog)
        if pattern_matches >= 1:  # If at least 3 battle UI elements are present
            logger.info(f"[Battle Detection] Battle UI detected with {pattern_matches} UI elements")
            self.currently_in_battle = True
            return True
            
        # Fallback to keyword detection
        battle_keywords = [
            "wants to fight", "sent out", "trainer", "battle", "attack", "fight",
            "used", "fainted", "effective", "damage", "defeated", "pokémon", "pokemon"
        ]
        
        # Check for battle keywords in dialog
        dialog_lower = dialog.lower()
        for keyword in battle_keywords:
            if keyword in dialog_lower:
                logger.info(f"[Battle Detection] Battle keyword detected: {keyword}")
                self.currently_in_battle = True
                return True
                
        return False
            
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
                        npc_global_coords = (g_x, g_y)
                        logger.debug(f"Found NPC {npc_id} at local ({local_x}, {local_y}) on map {current_map_id}, global ({g_x}, {g_y}).")
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
                reward_info += f"Use exit_menu tool to clear dialogs/menus\n"
                
                # Check if it might be a battle (persistent dialog that doesn't clear)
                if self.might_be_battle():
                    reward_info += f"⚠️ POSSIBLE BATTLE DETECTED - Consider using handle_battle tool\n"
            
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
            row = tool_input["row"]
            col = tool_input["col"]
            logger.info(f"[Navigation] Navigating to: ({row}, {col})")
            
            status, path = self.emulator.find_path(row, col)
            path_rewards = 0.0
            
            if path:
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
                reward_info += f"Use exit_menu tool to clear dialogs/menus\n"
                
                # Check if it might be a battle (persistent dialog that doesn't clear)
                if self.might_be_battle():
                    reward_info += f"⚠️ POSSIBLE BATTLE DETECTED - Consider using handle_battle tool\n"
            
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
                    {"type": "text", "text": f"\nNavigation result: {result}"},
                    {"type": "text", "text": f"\nGame state information from memory after your action:\n{memory_info}"},
                ],
            }
            
        elif tool_name == "exit_to_last_map":
            # Reverse recorded movements to return to previous map
            if not self.map_stack:
                return {"type":"tool_result","id": tool_call.id,"tool_use_id":tool_call.id,"content":[{"type":"text","text":"No previous map to exit to"}]}
            
            last_map, actions = self.map_stack.pop()
            inverse = {"up":"down","down":"up","left":"right","right":"left"}
            reverse_actions = [inverse[a] for a in reversed(actions) if a in inverse]
            
            # Process each step in the reverse path with rewards
            path_rewards = 0.0
            for direction in reverse_actions:
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
            
            self.current_map = last_map
            
            # Get game state and add reward information
            memory_info = self.emulator.get_state_from_memory()
            reward_info = f"\nREWARD INFORMATION:\n"
            reward_info += f"Path Reward: {path_rewards:.2f}\n"
            reward_info += f"Episode Reward: {self.current_episode_reward:.2f}\n"
            reward_info += f"Episode Step: {self.episode_step}/30\n"
            reward_info += f"Total Unique Tiles: {len(self.visited_tiles)}\n"
            
            return {
                "type": "tool_result",
                "id": tool_call.id,
                "tool_use_id": tool_call.id,
                "content": [
                    {"type": "text", "text": f"Exited to {last_map} via actions: {reverse_actions}"},
                    {"type": "text", "text": f"\nPath Reward: {path_rewards:.2f}"},
                    {"type": "text", "text": f"\nEpisode Reward: {self.current_episode_reward:.2f} (Step {self.episode_step}/30)"},
                    {"type": "text", "text": memory_info if memory_info else ""}
                ]
            }
            
        elif tool_name == "exit_menu":
            """Exit any active menu or dialog by pressing B multiple times."""
            logger.info("[Tool] Executing exit_menu tool")
            
            # Press B up to 5 times to exit menus
            b_press_count = 0
            max_presses = 5
            
            # First check if we're in a dialog/menu state
            in_dialog_before = self.is_in_non_navigation_state()
            
            for _ in range(max_presses):
                self.emulator.press_buttons(["b"], True)
                b_press_count += 1
                
                # Ensure emulator step after each button press to update game state
                self.emulator.step()
                
                # Check if we're out of dialog/menu state
                if not self.is_in_non_navigation_state():
                    # Press one more B after detecting exit, to ensure clean transition
                    self.emulator.press_buttons(["b"], True) 
                    self.emulator.step()
                    b_press_count += 1
                    break
            
            # If still in menu/dialog after max B presses, try pressing A as fallback
            if self.is_in_non_navigation_state() and b_press_count >= max_presses:
                logger.info("[Menu Exit] Still in menu after B presses, trying A button")
                self.emulator.press_buttons(["a"], True)
                self.emulator.step()
                
                # Check one more time if we're out
                if not self.is_in_non_navigation_state():
                    logger.info("[Menu Exit] Successfully exited with A button")
                
                # Always press B once more for clean exit
                self.emulator.press_buttons(["b"], True)
                self.emulator.step()
            
            # Increment episode step (menu exit counts as an action)
            self.episode_step += 1
            
            # Get game state information
            memory_info = self.emulator.get_state_from_memory()
            
            # Add reward information
            reward_info = f"\nREWARD INFORMATION:\n"
            reward_info += f"Menu Exit Action: No reward change (neutral action)\n"
            reward_info += f"Episode Reward: {self.current_episode_reward:.2f}\n"
            reward_info += f"Episode Step: {self.episode_step}/30\n"
            
            # Check if we successfully exited the menu
            in_dialog_after = self.is_in_non_navigation_state()
            exit_status = "Successfully exited menu/dialog" if not in_dialog_after and in_dialog_before else "No active menu/dialog detected or unable to exit completely"
            
            # Check if it might be a battle (persistent dialog that doesn't clear)
            battle_hint = ""
            if in_dialog_after and in_dialog_before and b_press_count >= max_presses:
                battle_hint = "\n⚠️ POSSIBLE BATTLE DETECTED - Dialog persists after multiple B presses. Try using the handle_battle tool!"
            
            # Combine memory info with reward info
            if memory_info:
                memory_info += reward_info
            else:
                memory_info = reward_info
            
            return {
                "type": "tool_result",
                "id": tool_call.id,
                "tool_use_id": tool_call.id,
                "content": [
                    {"type": "text", "text": f"MENU EXIT: Pressed B {b_press_count} times. {exit_status}{battle_hint}"},
                    {"type": "text", "text": f"\nEpisode Status: {self.current_episode_reward:.2f} reward | Step {self.episode_step}/30"},
                    {"type": "text", "text": f"\nGame state information after menu exit:\n{memory_info}"}
                ]
            }
            
        elif tool_name == "handle_battle":
            # Capture current battle dialog before advancing
            dialog_before = self.emulator.get_active_dialog() or ""
            status_text = dialog_before.strip().replace("\n", " ") if dialog_before else "(Battle dialog cleared)"
            thought_text = "In a battle, press A to play!" if dialog_before else ""
            # Press A to advance one text box (press_buttons includes necessary emulator ticks)
            self.emulator.press_buttons(["a"], True)
            self.episode_step += 1
            # Update last_message for UI using the captured text
            self.last_message = f"Battle Text: '{status_text}'" + (f" -- {thought_text}" if thought_text else "")
            # Return the captured text and guiding thought
            return {
                "type": "tool_result",
                "id": tool_call.id,
                "tool_use_id": tool_call.id,
                "content": [
                    {"type": "text", "text": f"Battle Text: \"{status_text}\""},
                    {"type": "text", "text": thought_text}
                ]
            }
            
        elif tool_name == "check_bounds":  # Add this block
            return self.nav.bounds_check_tool(tool_call) 
                                
        else:
            logger.error(f"Unknown tool called: {tool_name}")
            return {
                "type": "tool_result",
                "id": tool_call.id,
                "tool_use_id": tool_call.id,
                "content": [
                    {"type": "text", "text": f"Error: Unknown tool '{tool_name}'"}
                ],
            }
    
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
    
    
    def step(self):
        """Execute a single step with strict timing controls and navigation."""
        import time
        import logging
        import random
        
        # Get game logger
        logger = logging.getLogger(__name__)
        
        # Log the start of step execution
        logger.info("Starting step execution")
        
        # Check for any active dialog and current battle state
        dialog = self.emulator.get_active_dialog()
        in_battle = self.might_be_battle()
        
        # Check if a battle just finished
        battle_just_finished = self.was_in_battle and not in_battle
        
        # Log dialog/battle state
        if dialog:
            logger.info(f"Dialog detected: {dialog[:30]}...")
            if in_battle:
                logger.info("Battle state detected")
            else:
                logger.info("Dialog state detected")

                # If a non-battle dialog is active, check for adjacent NPC interaction
                # Check if it's a non-battle dialog that is not the Start menu
                # Assuming non-battle dialog + not persistent location implies interaction
                if not in_battle and self.persistent_location_count < 3: # Add a check for persistent location
                     interacted_npc_key = self.detect_npc_interaction()
                     if interacted_npc_key:
                         # Mark the adjacent NPC as interacted using global coordinates
                         # Need to get the global coordinates from the NPC key (map_id, sprite_index)
                         map_id, sprite_index = interacted_npc_key
                         npc_global_coords = None
                         collision_data = self.emulator.get_collision_map()
                         if collision_data and collision_data.get("sprite_data"):
                             for sprite_info in collision_data["sprite_data"]:
                                 if sprite_info.get("map_id") == map_id and sprite_info.get("sprite_index") == sprite_index:
                                     local_x = sprite_info.get("x")
                                     local_y = sprite_info.get("y")
                                     current_map_id = self.emulator.reader.read_current_map_id()
                                     if local_x is not None and local_y is not None and current_map_id is not None:
                                          g_y, g_x = self.emulator.local_to_global(local_y, local_x, current_map_id)
                                          npc_global_coords = (g_x, g_y)
                                          break

                         if npc_global_coords:
                             self.interacted_npcs.add(npc_global_coords) # Add global coords to set
                             logger.info(f"[NPC Interaction] Marked adjacent NPC {interacted_npc_key} (global {npc_global_coords}) as interacted due to non-battle dialog.")
                         else:
                             logger.warning(f"[NPC Interaction] Could not find global coordinates for faced NPC {interacted_npc_key} despite non-battle dialog.")
        else:
            logger.info("No dialog detected")
        
        # Take action based on state
        try:
            if dialog:
                if in_battle:
                    # If entering a new battle, try to identify the opponent NPC
                    if not self.was_in_battle:
                         # Attempt to detect the NPC currently being faced/interacted with
                         # This assumes the battle was initiated by interacting with an NPC
                         npc_key = self.detect_npc_interaction()
                         if npc_key:
                              self._last_battled_npc_key = npc_key
                              logger.info(f"[Battle Start] Detected potential battled NPC: {npc_key}")

                    # Handle battle
                    logger.info("Processing battle with handle_battle tool")
                    class FakeToolCall:
                        def __init__(self, name, input_data):
                            self.name = name
                            self.input = input_data
                            self.id = str(uuid.uuid4())
                            self.type = "function"
                    
                    battle_result = self.process_tool_call(FakeToolCall("handle_battle", {}))
                    self.message_history.append({"role": "user", "content": [battle_result]})
                    
                    battle_text = dialog.replace('\n', ' ')[:50] if dialog else "Battle action"
                    logger.info(f"Battle action performed: {battle_text}")
                    self.last_message = f"Battle Text: '{battle_text}'"
                else:
                    # Non-battle dialog - exit menu
                    logger.info("Processing dialog with exit_menu tool")
                    class FakeToolCall:
                        def __init__(self, name, input_data):
                            self.name = name
                            self.input = input_data
                            self.id = str(uuid.uuid4())
                            self.type = "function"
                    
                    exit_result = self.process_tool_call(FakeToolCall("exit_menu", {}))
                    self.message_history.append({"role": "user", "content": [exit_result]})
                    
                    dialog_text = dialog.replace('\n', ' ')[:50] if dialog else "Dialog"
                    logger.info(f"Dialog action performed: {dialog_text}")
                    self.last_message = f"Dialog: {dialog_text}"
            else:
                # No dialog or battle - time to move and explore!
                logger.info("No dialog or battle detected. Exploring the overworld")

                # If a battle just finished and we had a tracked NPC, mark them as interacted
                if battle_just_finished and self._last_battled_npc_key:
                    map_id, sprite_index = self._last_battled_npc_key
                    # Call update_npc_interaction_reward which now handles global coord conversion
                    self.update_npc_interaction_reward(sprite_index, map_id)
                    self._last_battled_npc_key = None # Clear the tracked NPC after marking

                # Get valid moves
                valid_moves = self.get_valid_moves()
                if valid_moves:
                    # Choose a move from valid directions
                    move_direction = random.choice(valid_moves)
                    logger.info(f"Exploring: Moving {move_direction}")
                    
                    # Create fake tool call for movement
                    class FakeToolCall:
                        def __init__(self, name, input_data):
                            self.name = name
                            self.input = input_data
                            self.id = str(uuid.uuid4())
                            self.type = "function"
                    
                    # Execute the movement
                    move_result = self.process_tool_call(FakeToolCall("press_buttons", {"buttons": [move_direction], "wait": True}))
                    self.message_history.append({"role": "user", "content": [move_result]})
                    
                    # Update the last message
                    current_location = self.emulator.get_location() or "Unknown"
                    self.last_message = f"Exploring {current_location} (moving {move_direction})"
                else:
                    logger.info("No valid moves available. Trying to press A to interact")
                    
                    # Press A to interact with objects or NPCs
                    class FakeToolCall:
                        def __init__(self, name, input_data):
                            self.name = name
                            self.input = input_data
                            self.id = str(uuid.uuid4())
                            self.type = "function"
                    
                    interact_result = self.process_tool_call(FakeToolCall("press_buttons", {"buttons": ["a"], "wait": True}))
                    self.message_history.append({"role": "user", "content": [interact_result]})
                    
                    # Update the last message
                    current_location = self.emulator.get_location() or "Unknown"
                    self.last_message = f"Interacting in {current_location}"
        except Exception as e:
            logger.error(f"Error during step execution: {e}", exc_info=True)
        
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
        
        # CRITICAL: Enforce minimum 3-second delay after any action
        logger.info("Enforcing 3-second delay between actions")
        time.sleep(3.0)
        
        logger.info("Step execution completed")
            
        # Log the current collision map (ASCII visualization) and raw data
        collision_data = self.emulator.get_collision_map()
        if collision_data:
            # Log ASCII map with visit counts and entities
            collision_ascii_map = self.emulator.format_collision_map_with_counts(collision_data)
            logger.info(f"[Collision Map Step {self.episode_step}/{self.episode_step}]\n{collision_ascii_map}")
            # Make map visible to Grok by adding to message history
            self.message_history.append({"role": "assistant", "content": collision_ascii_map})

            # Log raw collision data for debugging NPC interaction
            # Note: collision_map_raw is not a separate attribute, it's the structure returned by get_collision_map()
            logger.debug(f"[Raw Collision Data Step {self.episode_step}/{self.episode_step}]\n{collision_data}")

        self.prev_coordinates = current_location

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