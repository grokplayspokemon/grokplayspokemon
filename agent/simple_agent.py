import logging
logger = logging.getLogger(__name__)

import base64
import copy
import io
import json
import os
import requests
import time
import glob
from pathlib import Path
import uuid

# Make Google imports optional
try:
    from google import genai
    from google.genai.types import Tool, GenerateContentConfig, GoogleSearch
    GOOGLE_AVAILABLE = True
except ImportError:
    GOOGLE_AVAILABLE = False

# Remove direct config imports if they are now passed via cfg object
# from config import MAX_TOKENS, MODEL_NAME, TEMPERATURE # No longer needed directly
from agent.prompts import SYSTEM_PROMPT, SUMMARY_PROMPT
from agent.tools import AVAILABLE_TOOLS

from agent.emulator import Emulator
from anthropic import Anthropic
from agent.memory_reader import PokemonRedReader

# Auto-save directory constant
from config import SAVE_STATE_DIR

def get_screenshot_base64(screenshot, upscale=1):
    """Convert PIL image to base64 string."""
    # Resize if needed
    if upscale > 1:
        new_size = (screenshot.width * upscale, screenshot.height * upscale)
        screenshot = screenshot.resize(new_size)

    # Convert to base64
    buffered = io.BytesIO()
    screenshot.save(buffered, format="PNG")
    return base64.standard_b64encode(buffered.getvalue()).decode()


class SimpleAgent:
    def __init__(self, cfg, app=None): # Accept cfg object
        """Initialize the simple agent.

        Args:
            cfg: Configuration object/namespace containing settings
            app: FastAPI app instance for state management
        """
        self.emulator = Emulator(cfg.rom_path, cfg.emulator_headless, cfg.emulator_sound)
        self.emulator.initialize()  # Initialize the emulator
        
        # Save-state loading is managed by the application startup logic
        
        # Set provider and model from config
        self.provider = cfg.llm_provider
        self.model_name = cfg.llm_model
        self.temperature = cfg.llm_temperature
        self.max_tokens = cfg.llm_max_tokens
        self.screenshot_upscale = cfg.screenshot_upscale
        
        # Initialize provider-specific clients
        if self.provider == 'anthropic':
            api_key = os.getenv("ANTHROPIC_API_KEY")
            if not api_key:
                 raise ValueError("ANTHROPIC_API_KEY environment variable not set for Anthropic provider")
            self.anthropic_client = Anthropic(api_key=api_key)
        elif self.provider == 'openai':
            try:
                from openai import OpenAI
                api_key = os.getenv("OPENAI_API_KEY")
                if not api_key:
                    raise ValueError("OPENAI_API_KEY environment variable not set for OpenAI provider")
                self.openai_client = OpenAI(api_key=api_key)
            except ImportError:
                logger.error("OpenAI Python package not installed. Install with: pip install openai")
                raise
        elif self.provider == 'grok':
            # For Grok, we'll use direct API calls via the requests library
            api_key = os.getenv("XAI_API_KEY")
            if not api_key:
                raise ValueError("XAI_API_KEY environment variable not set for Grok provider")
            self.xai_api_key = api_key
        else:
            raise ValueError(f"Unsupported provider: {self.provider}")
            
        # Google Client is optional
        self.google_client = None
        if GOOGLE_AVAILABLE:
            try:
                self.google_client = genai.Client()
            except Exception as e:
                logger.warning(f"Failed to initialize Google client: {e}")
            
        self.running = True
        self.message_history = [{"role": "user", "content": "You may now begin playing."}]
        self.max_history = cfg.max_history # Use config value
        self.last_message = "Game starting..."  # Initialize last message
        self.app = app  # Store reference to FastAPI app
        self.use_overlay = cfg.use_overlay # Use config value
        
        # Modify system prompt if overlay is enabled
        if self.use_overlay:
            self.system_prompt = SYSTEM_PROMPT + '''
            There is a color overlay on the tiles that shows the following:

            🟥 Red tiles for walls/obstacles
            🟩 Green tiles for walkable paths
            🟦 Blue tiles for NPCs/sprites
            🟨 Yellow tile for the player with directional arrows (↑↓←→)
            '''
        else:
            self.system_prompt = SYSTEM_PROMPT

        # Initialize dialog tracking to prevent menu loops
        self.prev_dialog_text = None
        self.dialog_advance_count = 0
        # Track completed tool calls (steps) for walkthrough progress
        self.completed_steps = []
        # Track map transitions and movement logs
        self.map_stack = []  # stack of (map_name, actions) for backtracking
        self.move_log = []   # record movement buttons pressed since last map change
        self.current_map = None  # name of the current map

    def get_frame(self) -> bytes:
        """Get the current game frame as PNG bytes.
        
        Returns:
            bytes: PNG-encoded screenshot of the current frame with optional tile overlay
        """
        screenshot = self.emulator.get_screenshot_with_overlay() if self.use_overlay else self.emulator.get_screenshot()
        # Convert PIL image to PNG bytes
        buffered = io.BytesIO()
        screenshot.save(buffered, format="PNG")
        return buffered.getvalue()

    def get_last_message(self) -> str:
        """Get Grok's most recent message.
        
        Returns:
            str: The last message from Grok, or a default message if none exists
        """
        return self.last_message

    def process_tool_call(self, tool_call):
        """Process a single tool call."""
        tool_name = tool_call.name
        tool_input = tool_call.input
        # Record this tool use for walkthrough tracking
        self.completed_steps.append(tool_name)
        logger.info(f"Processing tool call: {tool_name}")

        if tool_name == "press_buttons":
            # Track movement actions for possible map transitions
            for btn in tool_input.get("buttons", []):
                if btn in ["up", "down", "left", "right"]:
                    self.move_log.append(btn)
            buttons = tool_input["buttons"]
            wait = tool_input.get("wait", True)
            logger.info(f"[Buttons] Pressing: {buttons} (wait={wait})")
            
            result = self.emulator.press_buttons(buttons, wait)
            # Detect map change and record movement log
            new_map = self.emulator.get_location()
            if self.current_map is None:
                self.current_map = new_map
            elif new_map != self.current_map:
                # Save the sequence of movements and clear log
                self.map_stack.append((self.current_map, self.move_log.copy()))
                self.move_log.clear()
                self.current_map = new_map
            
            # Get a fresh screenshot after executing the buttons with tile overlay
            screenshot = self.emulator.get_screenshot_with_overlay() if self.use_overlay else self.emulator.get_screenshot()
            screenshot_b64 = get_screenshot_base64(screenshot, upscale=self.screenshot_upscale) # Use config upscale
            
            # Get game state from memory after the action
            memory_info = self.emulator.get_state_from_memory()
            
            # Log the memory state after the tool call
            logger.info(f"[Memory State after action]")
            logger.info(memory_info)
            
            collision_map = self.emulator.get_collision_map()
            if collision_map:
                logger.info(f"[Collision Map after action]\n{collision_map}")
            
            # Return tool result as a dictionary
            return {
                "type": "tool_result",
                "tool_use_id": tool_call.id,
                "content": [
                    {"type": "text", "text": f"Pressed buttons: {', '.join(buttons)}"},
                    {"type": "text", "text": "\nHere is a screenshot of the screen after your button presses:"},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": screenshot_b64,
                        },
                    },
                    {"type": "text", "text": f"\nGame state information from memory after your action:\n{memory_info}"},
                ],
            }
        elif tool_name == "navigate_to":
            row = tool_input["row"]
            col = tool_input["col"]
            logger.info(f"[Navigation] Navigating to: ({row}, {col})")
            
            status, path = self.emulator.find_path(row, col)
            if path:
                for direction in path:
                    self.emulator.press_buttons([direction], True)
                result = f"Navigation successful: followed path with {len(path)} steps"
                # Improved warp traversal: if warp tile, press direction until warp triggers (max 3 attempts)
                if self.emulator.is_warp_tile(row, col):
                    last_dir = path[-1]
                    previous_map = self.emulator.get_location()
                    warp_attempts = 0
                    result += f", stepping onto warp and pressing '{last_dir}' to travel"
                    while warp_attempts < 3:
                        self.emulator.press_buttons([last_dir], True)
                        warp_attempts += 1
                        current_map = self.emulator.get_location()
                        if current_map != previous_map:
                            result += f", warp succeeded to {current_map}"
                            break
                    else:
                        result += ", but warp did not trigger after multiple attempts"
            else:
                result = f"Navigation failed: {status}"
            
            # Get a fresh screenshot after executing the navigation with tile overlay
            screenshot = self.emulator.get_screenshot_with_overlay()
            screenshot_b64 = get_screenshot_base64(screenshot, upscale=self.screenshot_upscale)
            
            # Get game state from memory after the action
            memory_info = self.emulator.get_state_from_memory()
            
            # Log the memory state after the tool call
            logger.info(f"[Memory State after action]")
            logger.info(memory_info)
            
            collision_map = self.emulator.get_collision_map()
            if collision_map:
                logger.info(f"[Collision Map after action]\n{collision_map}")
            
            # Return tool result as a dictionary
            return {
                "type": "tool_result",
                "tool_use_id": tool_call.id,
                "content": [
                    {"type": "text", "text": f"Navigation result: {result}"},
                    {"type": "text", "text": "\nHere is a screenshot of the screen after navigation:"},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": screenshot_b64,
                        },
                    },
                    {"type": "text", "text": f"\nGame state information from memory after your action:\n{memory_info}"},
                ],
            }
        elif tool_name == "talk_to_npcs":
            logger.info("[Talk to NPCs] Initiating NPC interactions")
            sprite_positions = self.emulator.get_sprites()
            interactions = []
            for col, row in sprite_positions:
                # Navigate to NPC position
                status, path = self.emulator.find_path(row, col)
                if path:
                    for direction in path:
                        self.emulator.press_buttons([direction], True)
                    interactions.append(f"Navigated to NPC at ({row}, {col})")
                    # Talk to NPC
                    self.emulator.press_buttons(["a"], True)
                    interactions.append(f"Talked to NPC at ({row}, {col})")
                else:
                    interactions.append(f"Failed to navigate to NPC at ({row}, {col}): {status}")
            # Capture screenshot and memory state after interactions
            screenshot = self.emulator.get_screenshot_with_overlay() if self.use_overlay else self.emulator.get_screenshot()
            screenshot_b64 = get_screenshot_base64(screenshot, upscale=self.screenshot_upscale)
            memory_info = self.emulator.get_state_from_memory()
            collision_map = self.emulator.get_collision_map()
            content = [{"type": "text", "text": "\n".join(interactions)}]
            content.append({"type": "text", "text": "\nHere is a screenshot after talking to NPCs:"})
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": screenshot_b64,
                },
            })
            content.append({"type": "text", "text": f"\nGame state information:\n{memory_info}"})
            if collision_map and self.provider != 'grok':
                content.append({"type": "text", "text": f"\nCollision Map:\n{collision_map}"})
            return {
                "type": "tool_result",
                "tool_use_id": tool_call.id,
                "content": content,
            }
        elif tool_name == "exit_to_last_map":
            # Reverse recorded movements to return to previous map
            if not self.map_stack:
                return {"type":"tool_result","tool_use_id":tool_call.id,"content":[{"type":"text","text":"No previous map to exit to"}]}
            last_map, actions = self.map_stack.pop()
            inverse = {"up":"down","down":"up","left":"right","right":"left"}
            reverse_actions = [inverse[a] for a in reversed(actions) if a in inverse]
            for direction in reverse_actions:
                self.emulator.press_buttons([direction], True)
            self.current_map = last_map
            return {"type":"tool_result","tool_use_id":tool_call.id,"content":[{"type":"text","text":f"Exited to {last_map} via actions: {reverse_actions}"}]}
        elif tool_name == "fetch_url":
            url = tool_input.get("url")
            try:
                resp = requests.get(url)
                content_text = resp.text
            except Exception as e:
                content_text = f"Error fetching URL: {e}"
            return {
                "type": "tool_result",
                "tool_use_id": tool_call.id,
                "content": [{"type": "text", "text": content_text}]
            }
        else:
            logger.error(f"Unknown tool called: {tool_name}")
            return {
                "type": "tool_result",
                "tool_use_id": tool_call.id,
                "content": [
                    {"type": "text", "text": f"Error: Unknown tool '{tool_name}'"}
                ],
            }

    def step(self):
        """Execute a single step of the agent's decision-making process."""
        # Auto-exit from any interior (non-overworld) map by reversing moves
        try:
            reader = PokemonRedReader(self.emulator.pyboy.memory)
            tileset = reader.read_tileset().upper()
            if tileset != "OVERWORLD" and (not self.completed_steps or self.completed_steps[-1] != "exit_to_last_map"):
                # Create and process a fake exit_to_last_map call
                class FakeToolCallExit:
                    def __init__(self, id):
                        self.name = "exit_to_last_map"
                        self.input = {}
                        self.id = id
                        self.type = "function"
                fake_call = FakeToolCallExit(str(uuid.uuid4()))
                result = self.process_tool_call(fake_call)
                # Append tool result properly to message history with a role to avoid missing 'role'
                self.message_history.append({"role": "user", "content": [result]})
                self.last_message = "Auto-exited interior map via exit_to_last_map"
                return
        except Exception as e:
            logger.warning(f"Failed to auto-exit interior map: {e}")
        # Handle active dialogs: detect menus and exit, otherwise advance lines with A
        try:
            dialog_text = self.emulator.get_active_dialog()
            if dialog_text:
                # Track repeated dialog lines
                if dialog_text == self.prev_dialog_text:
                    self.dialog_advance_count += 1
                else:
                    self.prev_dialog_text = dialog_text
                    self.dialog_advance_count = 0
                # If dialog persists too long, exit menu
                if self.dialog_advance_count >= 20:
                    logger.info("Detected prolonged dialog, exiting menu via B")
                    self.emulator.press_buttons(["b"], True)
                    self.last_message = "Exited menu after too many dialog advances"
                    self.message_history.append({"role": "assistant", "content": self.last_message})
                    # Reset tracking
                    self.prev_dialog_text = None
                    self.dialog_advance_count = 0
                    return
                # Detect common menu prompts (e.g., Pokémon menu) and fully exit via B
                lower = dialog_text.upper()
                menu_keys = ["CHOOSE A POK", "STATS", "CANCEL", "PKM", "POKÉMON", "BAG", "ITEM"]
                if any(k in lower for k in menu_keys):
                    logger.info("Detected menu context, clearing dialogs via repeated B presses")
                    # Press B until no dialog or menu remains
                    while self.emulator.get_active_dialog():
                        self.emulator.press_buttons(["b"], True)
                    self.last_message = "Exited menu via repeated B presses"
                    self.message_history.append({"role": "assistant", "content": self.last_message})
                    # Reset tracking
                    self.prev_dialog_text = None
                    self.dialog_advance_count = 0
                    return
                # Normal dialog advancement
                logger.info(f"[Dialog] Detected active dialog: '{dialog_text}'. Advancing via A.")
                # Record dialog text for context
                self.message_history.append({"role": "assistant", "content": f"NPC says: {dialog_text}"})
                # Press A to advance dialog
                self.emulator.press_buttons(["a"], True)
                # If A did not change dialog, try B then A
                new_dialog = self.emulator.get_active_dialog()
                if new_dialog == dialog_text:
                    logger.info("A did not advance dialog, trying B then A")
                    self.emulator.press_buttons(["b"], True)
                    self.emulator.press_buttons(["a"], True)
                # Record action and skip LLM for this step
                self.last_message = "Advanced dialog with A"
                self.message_history.append({"role": "assistant", "content": self.last_message})
                return
        except Exception as e:
            logger.warning(f"Failed to handle dialog: {e}")
        try:
            # Prepare message history and include latest memory state for context
            messages = copy.deepcopy(self.message_history)
            # Provide map context (map ID, name, size, warp info) to LLM
            try:
                from game_data.constants import MAP_DICT, WARP_DICT
                map_name = self.emulator.get_location()
                map_info = MAP_DICT.get(map_name, {})
                map_id = map_info.get('map_id', 'unknown')
                width = map_info.get('width', '?')
                height = map_info.get('height', '?')
                warps = WARP_DICT.get(map_name, [])
                warp_descs = []
                for w in warps:
                    grid_row = w['y'] // 2
                    grid_col = w['x'] // 2
                    if w['y'] == height - 1:
                        direction = 'down'
                    elif w['y'] == 0:
                        direction = 'up'
                    elif w['x'] == 0:
                        direction = 'left'
                    elif w['x'] == width - 1:
                        direction = 'right'
                    else:
                        direction = 'unknown'
                    warp_descs.append(f"({grid_row},{grid_col}) to {w['target_map_name']} via {direction}")
                warp_info = ', '.join(warp_descs) if warp_descs else 'none'
                messages.insert(1, {
                    'role': 'system',
                    'content': f"MAP INFO: id={map_id}, name={map_name}, size={width}x{height}, warps: {warp_info}" 
                })
            except Exception as e:
                logger.warning(f"Failed to fetch map context: {e}")
            # Inject NPC context: visible NPCs this map and those already interacted with
            try:
                self.emulator.update_seen_npcs()
                visible_npcs = self.emulator.get_npcs_in_range()
                interacted = sorted(self.emulator.get_seen_npcs())
                messages.insert(2, {
                    'role': 'system',
                    'content': f"NPCs on map: {visible_npcs}. NPCs already interacted: {interacted}."
                })
                # Inject completed steps history for walkthrough planning
                messages.insert(3, {
                    'role': 'system',
                    'content': f"Completed steps: {self.completed_steps}"
                })
            except Exception as e:
                logger.warning(f"Failed to inject NPC context: {e}")

            # Inject screenshot/collision only for non-Grok providers
            if self.provider != 'grok':
                if len(messages) >= 3:
                    if messages[-1]["role"] == "user" and isinstance(messages[-1]["content"], list) and messages[-1]["content"]:
                        if messages[-1]["content"][-1].get("type") == "tool_result":
                            screenshot = self.emulator.get_screenshot_with_overlay() if self.use_overlay else self.emulator.get_screenshot()
                            screenshot_b64 = get_screenshot_base64(screenshot, upscale=self.screenshot_upscale)
                            collision_map = self.emulator.get_collision_map()
                            messages[-1]["content"].append({"type": "text", "text": "\nHere is a screenshot of the current screen:"})
                            messages[-1]["content"].append({"type": "image", "source": {"type": "base64","media_type": "image/png","data": screenshot_b64}})
                            if collision_map:
                                messages[-1]["content"].append({"type": "text", "text": f"\nCollision Map:\n{collision_map}"})
                else:
                    screenshot = self.emulator.get_screenshot_with_overlay() if self.use_overlay else self.emulator.get_screenshot()
                    screenshot_b64 = get_screenshot_base64(screenshot, upscale=self.screenshot_upscale)
                    collision_map = self.emulator.get_collision_map()
                    messages.append({"role": "user","content": [{"type": "text","text": "Here is a screenshot of the current screen:"},{"type": "image","source": {"type": "base64","media_type": "image/png","data": screenshot_b64}}]})
                    if collision_map:
                        messages[-1]["content"].append({"type": "text","text": f"\nCollision Map:\n{collision_map}"})
            
            # Check for summarization
            if len(messages) > self.max_history:
                self.summarize_history()
                messages = copy.deepcopy(self.message_history) # Use the summarized history
                # Append the latest screenshot again after summarization
                screenshot = self.emulator.get_screenshot_with_overlay() if self.use_overlay else self.emulator.get_screenshot()
                screenshot_b64 = get_screenshot_base64(screenshot, upscale=self.screenshot_upscale)
                collision_map = self.emulator.get_collision_map()
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Here is a screenshot of the current screen:"},
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": screenshot_b64,
                                },
                            },
                        ],
                    }
                )
                if collision_map:
                    messages[-1]["content"].append(
                        {"type": "text", "text": f"\nCollision Map:\n{collision_map}"}
                    )

            # # --- Call LLM based on provider ---
            # if self.provider == 'anthropic':
            #     # Anthropic API call
            #     response = self.anthropic_client.messages.create(
            #         model=self.model_name,
            #         max_tokens=self.max_tokens, # Use config value
            #         temperature=self.temperature, # Use config value
            #         system=self.system_prompt,
            #         messages=messages,
            #         tools=AVAILABLE_TOOLS,
            #         tool_choice={"type": "auto"},
            #     )
            #     # ... (rest of Anthropic response handling)
            # elif self.provider == 'openai':
            #      # OpenAI API call
            #      try:
            #          # Map tools to OpenAI format if necessary
            #          openai_tools = []
            #          for tool in AVAILABLE_TOOLS:
            #              openai_tools.append({"type": "function", "function": tool})

            #          # Map message format
            #          openai_messages = []
            #          for msg in messages:
            #              if msg["role"] == "user":
            #                  # Handle complex content (text + image)
            #                  if isinstance(msg["content"], list):
            #                      new_content = []
            #                      for item in msg["content"]:
            #                          if isinstance(item, dict):
            #                              if item.get("type") == "text":
            #                                  new_content.append({"type": "text", "text": item["text"]})
            #                              elif item.get("type") == "image":
            #                                  # Convert base64 image source for OpenAI
            #                                  img_data = item["source"]["data"]
            #                                  media_type = item["source"]["media_type"]
            #                                  new_content.append({"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{img_data}"}})
            #                      openai_messages.append({"role": "user", "content": new_content})
            #                  else:
            #                     # Simple text content
            #                     openai_messages.append({"role": "user", "content": msg["content"]})
            #              elif msg["role"] == "assistant":
            #                  # Check for tool calls in assistant message
            #                  tool_calls = []
            #                  content = msg.get("content", "")
            #                  if msg.get("tool_calls"): 
            #                      for tc in msg["tool_calls"]:
            #                          tool_calls.append({"id": tc["id"], "type": "function", "function": {"name": tc["name"], "arguments": json.dumps(tc["input"])}})
            #                  openai_messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls if tool_calls else None})
            #              elif msg["role"] == "tool": # Role used by Anthropic for tool results
            #                  # Map tool result back for OpenAI
            #                  openai_messages.append({"role": "tool", "tool_call_id": msg["tool_use_id"], "content": json.dumps(msg["content"])})
                             
            #          response = self.openai_client.chat.completions.create(
            #              model=self.model_name,
            #              messages=openai_messages,
            #              tools=openai_tools,
            #              tool_choice="auto",
            #              max_tokens=self.max_tokens, # Use config value
            #              temperature=self.temperature # Use config value
            #          )
                     
            #          # Process OpenAI response
            #          response_message = response.choices[0].message
            #          text_content = response_message.content or ""
            #          tool_calls = []
                     
            #          if response_message.tool_calls:
            #              logger.info(f"OpenAI response has tool calls: {response_message.tool_calls}")
            #              for tc in response_message.tool_calls:
            #                  # Convert back to Anthropic-like format for processing
            #                  tool_calls.append(
            #                      {
            #                          "id": tc.id,
            #                          "type": "function", # Match Anthropic type key
            #                          "name": tc.function.name,
            #                          "input": json.loads(tc.function.arguments),
            #                      }
            #                  )
                     
            #          # Update last message and history
            #          self.last_message = text_content or "No text response"
            #          logger.info(f"[Text] {self.last_message}")
            #          assistant_message = {"role": "assistant", "content": text_content, "tool_calls": tool_calls if tool_calls else []}
            #          self.message_history.append(assistant_message)
                     
            #          # Process tool calls if any
            #          if tool_calls:
            #              tool_results = []
            #              for tool_call in tool_calls:
            #                  # Re-wrap tool_call to match expected structure for process_tool_call
            #                  class FakeToolCall:
            #                      def __init__(self, data):
            #                          self.name = data["name"]
            #                          self.input = data["input"]
            #                          self.id = data["id"]
            #                          self.type = data["type"]
                                     
            #                  result = self.process_tool_call(FakeToolCall(tool_call))
            #                  tool_results.append(result)
                             
            #              self.message_history.append({"role": "user", "content": tool_results})
                     
            #      except Exception as e:
            #          logger.error(f"Error calling OpenAI API: {e}")
            #          self.last_message = f"Error: Could not get response from OpenAI: {e}"
                 
            if self.provider == 'grok':
                 # Grok API Call (using requests)
                 headers = {
                     "Authorization": f"Bearer {self.xai_api_key}",
                     "Content-Type": "application/json"
                 }
                 
                 # Format messages for Grok (ensure content is string)
                 grok_messages = []
                 for msg in messages:
                     role = msg["role"]
                     # Grok expects string content, flatten if necessary
                     if isinstance(msg.get("content"), list):
                         content_parts = []
                         for item in msg["content"]:
                             if isinstance(item, dict):
                                 if item.get("type") == "text":
                                     content_parts.append(item["text"])
                                 elif item.get("type") == "tool_result": # Handle tool results
                                     # Extract text from tool result content list
                                     tool_content_text = "".join([c.get("text","") for c in item.get("content",[]) if isinstance(c, dict)])
                                     content_parts.append(f"Tool Result ({item.get('tool_use_id', '')}): {tool_content_text}")
                                 elif item.get("type") == "image":
                                      content_parts.append("[IMAGE OMITTED FOR GROK]") # Grok doesn't handle images in API yet
                             else: # Simple string in list?
                                 content_parts.append(str(item))
                         content = "\n".join(content_parts)
                     else:
                         content = msg.get("content", "")
                     
                     # Grok uses 'tool' role for results, map 'user' role with tool results
                     if role == "user" and isinstance(msg.get("content"), list) and msg["content"] and msg["content"][0].get("type") == "tool_result":
                         role = "tool" # Map role for Grok
                         # Content is already flattened above
                     elif role == "tool": # Map Anthropic tool result role back to user for Grok history?
                         # Let's stick to Grok's expected roles if possible. Check docs.
                         # Assuming Grok handles 'tool' role for results directly.
                         pass 
                         
                     grok_messages.append({"role": role, "content": content})
                 
                 # Build tool definitions for Grok API
                 grok_tools = []
                 for tool in AVAILABLE_TOOLS:
                     grok_tools.append({
                         "type": "function",
                         "function": {
                             "name": tool["name"],
                             "description": tool.get("description", ""),
                             "parameters": tool["input_schema"]
                         }
                     })
                 
                 payload = {
                     "model": self.model_name,
                     "messages": grok_messages,
                     "tools": grok_tools,
                     "tool_choice": "auto",
                     "temperature": self.temperature, # Use config value
                     "max_tokens": self.max_tokens, # Use config value
                 }
                 
                 try:
                     api_response = requests.post("https://api.x.ai/v1/chat/completions", headers=headers, json=payload)
                     
                     # Process response
                     logger.info(f"Raw Grok response: {api_response.text}") # Log the raw JSON text
                     api_response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
                     
                     response_json = api_response.json()
                     logger.info(f"Parsed Grok JSON: {response_json}") # Log the parsed dictionary
                     
                     # Extract message and tool calls
                     message = response_json["choices"][0]["message"]
                     text_content = message.get("content", "")
                     # Grok returns tool calls in a list under the 'tool_calls' key
                     grok_tool_calls = message.get("tool_calls", []) # This should be a list of dicts
                     
                     # Update last message
                     self.last_message = text_content or "No text response"
                     logger.info(f"[Text] {self.last_message}")
                     
                     # Extract tool calls in Anthropic-like format for processing
                     tool_calls_for_processing = []
                     logger.info(f"Attempting to extract tool calls from grok_tool_calls: {grok_tool_calls}")
                     if grok_tool_calls: 
                         for tc in grok_tool_calls:
                             if tc.get("type") == "function":
                                 # Convert Grok's format to match process_tool_call expectation
                                 tool_calls_for_processing.append({
                                     "id": tc.get("id"), 
                                     "type": tc.get("type"), # Should be 'function'
                                     "name": tc["function"]["name"],
                                     "input": json.loads(tc["function"]["arguments"])
                                 })
                     
                     # Append assistant message to history (including potential tool calls)
                     assistant_message = {"role": "assistant", "content": text_content, "tool_calls": tool_calls_for_processing}
                     self.message_history.append(assistant_message)
                     
                     # Process tool calls if any
                     if tool_calls_for_processing:
                         tool_results = []
                         for tool_call in tool_calls_for_processing:
                             # Re-wrap tool_call to match expected structure for process_tool_call
                             class FakeToolCall:
                                 def __init__(self, data):
                                     self.name = data["name"]
                                     self.input = data["input"]
                                     self.id = data["id"]
                                     self.type = data["type"]
                                     
                             result = self.process_tool_call(FakeToolCall(tool_call))
                             tool_results.append(result)
                             
                         # Append tool results as a user message (as Anthropic expects)
                         # Grok might expect a 'tool' role here, need to verify Grok API docs for tool result handling
                         self.message_history.append({"role": "user", "content": tool_results})
                             
                 except requests.exceptions.RequestException as e:
                     logger.error(f"Error calling Grok API: {e}")
                     if hasattr(e, 'response') and e.response is not None:
                         logger.error(f"Grok API Response Status: {e.response.status_code}")
                         logger.error(f"Grok API Response Body: {e.response.text}")
                         self.last_message = f"Error: Grok API request failed - {e.response.status_code} {e.response.text}"
                     else:
                         self.last_message = f"Error: Could not connect to Grok API: {e}"
                 except Exception as e:
                     logger.error(f"Unexpected error processing Grok response: {e}")
                     self.last_message = f"Error: Unexpected error processing Grok response: {e}"

            else:
                logger.error(f"Invalid provider configured: {self.provider}")
                self.last_message = f"Error: Invalid LLM provider '{self.provider}' configured."

        except Exception as e:
            logger.error(f"Error in agent step: {e}", exc_info=True)
            self.last_message = f"Error in agent step: {e}"

    def run(self, num_steps=1):
        """Main agent loop.

        Args:
            num_steps: Number of steps to run for
        """
        logger.info(f"Starting agent loop for {num_steps} steps")

        # Automatically press Start and A a few times to get past title screens
        logger.info("Automatically pressing Start and A to get past title screens...")
        self.emulator.press_buttons(["start"], True)
        time.sleep(1)
        self.emulator.press_buttons(["a"], True)
        time.sleep(1)
        self.emulator.press_buttons(["a"], True)
        time.sleep(1)
        
        steps_completed = 0
        while self.running and steps_completed < num_steps:
            try:
                self.step()
                steps_completed += 1
                logger.info(f"Completed step {steps_completed}/{num_steps}")

            except KeyboardInterrupt:
                logger.info("Received keyboard interrupt, stopping")
                self.running = False
            except Exception as e:
                logger.error(f"Error in agent loop: {e}")
                raise e

        if not self.running:
            self.emulator.stop()

        return steps_completed

    def summarize_history(self):
        """Generate a summary of the conversation history and replace the history with just the summary."""
        logger.info(f"[Agent] Generating conversation summary...")
        
        # Get a new screenshot for the summary
        screenshot = self.emulator.get_screenshot()
        screenshot_b64 = get_screenshot_base64(screenshot, upscale=2)
        
        # Create messages for the summarization request - pass the entire conversation history
        messages = copy.deepcopy(self.message_history) 

        if len(messages) >= 3:
            if messages[-1]["role"] == "user" and isinstance(messages[-1]["content"], list) and messages[-1]["content"]:
                messages[-1]["content"][-1]["cache_control"] = {"type": "ephemeral"}
            
            if len(messages) >= 5 and messages[-3]["role"] == "user" and isinstance(messages[-3]["content"], list) and messages[-3]["content"]:
                messages[-3]["content"][-1]["cache_control"] = {"type": "ephemeral"}

        # Add summary prompt
        if self.provider == 'anthropic':
            messages += [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": SUMMARY_PROMPT,
                        }
                    ],
                }
            ]
            
            # Get summary from Grok
            response = self.anthropic_client.messages.create(
                model=self.model_name,
                max_tokens=self.max_tokens,
                system=self.system_prompt,
                messages=messages,
                temperature=self.temperature
            )
            
            # Extract summary text
            summary_text = " ".join([block.text for block in response.content if block.type == "text"])
            
        elif self.provider == 'openai':
            # Format messages for OpenAI
            openai_messages = []
            for msg in messages:
                role = msg["role"]
                if role == "user":
                    role = "user"
                elif role == "assistant":
                    role = "assistant"
                else:
                    continue  # Skip other roles
                
                # Create content
                if isinstance(msg.get("content"), list):
                    # Complex content - flatten tool results and text
                    content_parts = []
                    for item in msg["content"]:
                        if isinstance(item, dict):
                            if item.get("type") == "text":
                                content_parts.append(item["text"])
                            elif item.get("type") == "tool_result":
                                # Extract text from tool_result content
                                result_content = item.get("content", [])
                                if isinstance(result_content, list):
                                    for result_item in result_content:
                                        if isinstance(result_item, dict) and result_item.get("type") == "text":
                                            content_parts.append(result_item["text"])
                                else:
                                    content_parts.append(str(result_content)) # Fallback for unexpected format
                    content = "\n".join(filter(None, content_parts)) # Join non-empty parts
                else:
                    # Simple content
                    content = str(msg.get("content", ""))
                
                openai_messages.append({"role": role, "content": content})
            
            # Add system message and summary prompt
            openai_messages.insert(0, {"role": "system", "content": self.system_prompt})
            openai_messages.append({"role": "user", "content": SUMMARY_PROMPT})
            
            # Call OpenAI API
            response = self.openai_client.chat.completions.create(
                model=self.model_name,
                messages=openai_messages,
                temperature=self.temperature,
            )
            
            # Extract summary text
            summary_text = response.choices[0].message.content
            
        elif self.provider == 'grok':
            # Format messages for Grok
            grok_messages = []
            for msg in messages:
                role = msg["role"]
                if role == "user":
                    role = "user"
                elif role == "assistant":
                    role = "assistant"
                else:
                    role = "user"  # Default to user for other roles
                
                # Create content
                if isinstance(msg.get("content"), list):
                    # Complex content - flatten tool results and text
                    content_parts = []
                    for item in msg["content"]:
                        if isinstance(item, dict):
                            if item.get("type") == "text":
                                content_parts.append(item["text"])
                            elif item.get("type") == "tool_result":
                                # Extract text from tool_result content
                                result_content = item.get("content", [])
                                if isinstance(result_content, list):
                                    for result_item in result_content:
                                        if isinstance(result_item, dict) and result_item.get("type") == "text":
                                            content_parts.append(result_item["text"])
                                else:
                                    content_parts.append(str(result_content)) # Fallback for unexpected format
                    content = "\n".join(filter(None, content_parts)) # Join non-empty parts
                else:
                    # Simple content
                    content = str(msg.get("content", ""))
                
                grok_messages.append({"role": role, "content": content})
            
            # Add system message and summary prompt
            grok_messages.insert(0, {"role": "system", "content": self.system_prompt})
            grok_messages.append({"role": "user", "content": SUMMARY_PROMPT})
            
            # Build the API request
            request_data = {
                "model": self.model_name,
                "messages": grok_messages,
                "temperature": self.temperature,
            }
            
            # Call Grok API
            response = requests.post(
                "https://api.x.ai/v1/chat/completions",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.xai_api_key}"
                },
                json=request_data
            )
            
            # Process response
            logger.info(f"Raw Grok response: {response.text}")
            if response.status_code != 200:
                raise Exception(f"Grok API error: {response.status_code} {response.text}")
            
            response_json = response.json()
            
            # Extract summary text
            summary_text = response_json["choices"][0]["message"].get("content", "")
        else:
            raise ValueError(f"Unsupported provider: {self.provider}")
        
        logger.info(f"[Agent] Game Progress Summary:")
        logger.info(f"{summary_text}")
        
        # Replace message history with just the summary
        self.message_history = [
            # Reset history with just the summary
            {
                "role": "assistant", 
                "content": [
                    {"type": "text", "text": f"CONVERSATION HISTORY SUMMARY: {summary_text}"}
                ]
            }
        ]
        
        # Update last message with summary
        self.last_message = f"Generated summary of conversation history ({len(messages)} messages)"

    def stop(self):
        """Stop the agent."""
        self.running = False
        self.emulator.stop()


if __name__ == "__main__":
    # Get the ROM path relative to this file
    current_dir = os.path.dirname(os.path.abspath(__file__))
    rom_path = os.path.join(os.path.dirname(current_dir), "pokemon.gb")

    # Create and run agent
    agent = SimpleAgent(rom_path)

    try:
        steps_completed = agent.run(num_steps=10)
        logger.info(f"Agent completed {steps_completed} steps")
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, stopping")
    finally:
        agent.stop()