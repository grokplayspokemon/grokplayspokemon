# simple_agent.py
import base64
import copy
import io
import logging
import os
from datetime import datetime
import json
import re # For _compact_history (if still used, otherwise remove)
import random # For introspection prompts
import time # For run loop pause in main test
from PIL import Image # For PIL Image processing

# OpenAI SDK for xAI
from openai import OpenAI
from openai.types.chat import ChatCompletionMessage, ChatCompletionMessageToolCall # For type hinting

# Project-specific imports
from agent.prompts import SYSTEM_PROMPT, SUMMARY_PROMPT, INTROSPECTION_PROMPTS # Assuming these are still relevant and INTROSPECTION_PROMPTS exists
from agent.grok_tool_implementations import AVAILABLE_TOOLS_LIST
from environment.environment import RedGymEnv, VALID_ACTIONS # Added VALID_ACTIONS import
from environment.wrappers.env_wrapper import EnvWrapper # Kept for type hint, but usage will be optional
from environment.environment_helpers.navigator import InteractiveNavigator
from environment.environment_helpers.quest_manager import QuestManager
# Corrected import for game state extraction and its type
from environment.grok_integration import extract_structured_game_state, GameState 
from dataclasses import asdict # For converting GameState to dict if needed

# Typing
from typing import Tuple, List, Dict, Any, Optional

# Helper to clean / deduplicate dialog strings for compact display
def _clean_dialog(raw: str) -> str:
    parts, seen = [], set()
    arrow = False
    for ln in raw.split("\n"): # Python's split, not regex \n
        t = ln.strip()
        if not t:
            continue
        if t == "▼":
            arrow = True
            continue
        if t in seen:
            continue
        seen.add(t)
        parts.append(t)
    combined = " / ".join(parts)
    if arrow and combined:
        combined += " ▼"
    return combined or raw.strip() or "None"

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# Utility for pretty‑printing message objects
def _pretty_json(obj) -> str:
    try:
        text = json.dumps(obj, indent=2, ensure_ascii=False)
        return text.replace("\\n", "\n") # Ensure JSON newlines are Python newlines for logging
    except Exception:
        return str(obj)

# Convert PIL image to base64 string
def get_screenshot_base64(screenshot_pil, upscale=1): # Assuming screenshot_pil is a PIL Image
    if screenshot_pil is None:
        logger.warning("get_screenshot_base64 received None image.")
        return None # Return None if no image is provided
    if upscale > 1:
        new_size = (screenshot_pil.width * upscale, screenshot_pil.height * upscale)
        screenshot_pil = screenshot_pil.resize(new_size)
    buffered = io.BytesIO()
    screenshot_pil.save(buffered, format="PNG")
    return base64.standard_b64encode(buffered.getvalue()).decode()

# Refactor SimpleAgent to be an action decision maker, not executor
# SimpleAgent analyzes game state, might return PATH_FOLLOW_ACTION (6)
class SimpleAgent:
    def _update_sidebar(self, line: str):
        raw = line.strip()
        if not raw or raw.startswith("##"): return
        norm = raw.lower()
        if norm == getattr(self, "_prev_sidebar_norm", None): return
        self.last_message = raw # For UI
        self._prev_sidebar_norm = norm

    def _trim_history(self):
        # Ensure system prompt is always first if present
        has_system_prompt = self.message_history and self.message_history[0]["role"] == "system"
        
        # Calculate how many messages to keep, accounting for system prompt
        effective_max_history = self.max_history_len
        if has_system_prompt:
            # max_history_len is the total, so if system is 1, others are max_history_len - 1
            # However, we want to keep `max_history_len` *including* the system prompt if present.
            # So, if system prompt is there, we can have `max_history_len - 1` other messages.
            # The number of messages *to keep* (excluding system) is `max_history_len - 1`
            # If total messages are `max_history_len`, and one is system, then `max_history_len-1` are non-system.
            # This means we check `current_non_system_messages_count` against `max_history_len -1`.
            
            # Let actual_max_non_system_messages = self.max_history_len - 1 (if system prompt exists)
            # else actual_max_non_system_messages = self.max_history_len
            limit_non_system = self.max_history_len - (1 if has_system_prompt else 0)
            if limit_non_system < 0: limit_non_system = 0 # Should not happen if max_history_len >= 1

        current_non_system_messages_count = len(self.message_history) - (1 if has_system_prompt else 0)

        if current_non_system_messages_count > limit_non_system:
            overflow = current_non_system_messages_count - limit_non_system
            start_delete_index = 1 if has_system_prompt else 0 # Deleting after system prompt, or from start
            del self.message_history[start_delete_index : start_delete_index + overflow]
            logger.debug(f"Trimmed {overflow} messages from history. Current non-system: {len(self.message_history) - (1 if has_system_prompt else 0)}. Total: {len(self.message_history)}")


    def _get_current_system_prompt_content(self) -> str:
        prompt_parts = [self.base_system_prompt]
        if self.history_summary:
            prompt_parts.append("\n\nCONVERSATION SUMMARY:\n" + self.history_summary)
        return "\n\n".join(prompt_parts)

    def __init__(
        self,
        reader: RedGymEnv, # Changed order, reader is primary
        quest_manager: QuestManager,
        navigator: InteractiveNavigator,
        env_wrapper: EnvWrapper = None, # Made optional, won't be used for execution
        xai_api_key: Optional[str] = None,
        xai_base_url: str = "https://api.x.ai/v1",
        model_name: str = "grok-3-mini",
        reasoning_effort: str = "low",
        temperature: float = 0.7,
        max_tokens_completion: int = 1024,
        max_history_len: int = 20,
        grok_enabled: bool = False,
        app=None
    ):
        assert reader is not None, "RedGymEnv (reader) must be provided"
        assert quest_manager is not None, "QuestManager must be provided"
        assert navigator is not None, "InteractiveNavigator must be provided"

        self.grok_enabled = grok_enabled
        self.env_wrapper = env_wrapper # Store for state reading only
        self.reader = reader
        self.quest_manager = quest_manager
        self.navigator = navigator
        
        # Only initialize grok-related components if grok is enabled
        if self.grok_enabled:
            self.xai_api_key = xai_api_key or os.environ.get("XAI_API_KEY")
            if not self.xai_api_key:
                raise ValueError("xAI API key not provided and not found in XAI_API_KEY environment variable.")
            
            self.client = OpenAI(api_key=self.xai_api_key, base_url=xai_base_url)
            self.model_name = model_name
            # Validate reasoning_effort for grok-3-mini models
            if "grok-3-mini" in self.model_name:
                if reasoning_effort not in ["low", "medium", "high"]:
                    logger.warning(f"Invalid reasoning_effort '{reasoning_effort}' for {self.model_name}. Defaulting to 'medium'.")
                    self.reasoning_effort = "medium"
                else:
                    self.reasoning_effort = reasoning_effort
            else:
                self.reasoning_effort = None # Not applicable for other models
                logger.info(f"Reasoning effort not applicable for model {self.model_name}.")

            self.temperature = temperature
            self.max_tokens_completion = max_tokens_completion
            
            # Tools definition for OpenAI API
            self.tools_definition = []
            for tool_data in AVAILABLE_TOOLS_LIST:
                if tool_data.get('declaration'):
                    self.tools_definition.append(tool_data['declaration'])
                else:
                    logger.warning(f"Tool '{tool_data.get('name', 'Unknown')}' is missing a 'declaration' and will not be available.")
            
            self.base_system_prompt = SYSTEM_PROMPT # From agent.prompts
            self.message_history: List[Dict[str, Any]] = [
                {"role": "system", "content": self._get_current_system_prompt_content()}
            ]
            # Max history_len includes the system prompt. So if max_history_len is 20, it's 1 system + 19 user/assistant/tool turns.
            self.max_history_len = max_history_len if max_history_len >=1 else 1
            self.history_summary = "" # Stores the latest summary text
            
            logger.info(f"SimpleAgent initialized for xAI. Model: {self.model_name}, Temp: {self.temperature}" +
                        (f", Reasoning: {self.reasoning_effort}" if self.reasoning_effort else ""))
            logger.info(f"Tools loaded: {[tool['function']['name'] for tool in self.tools_definition]}") # Corrected to use function name
        else:
            # Initialize minimal components for non-grok mode
            self.client = None
            self.model_name = "none"
            self.reasoning_effort = None
            self.temperature = 0.0
            self.max_tokens_completion = 0
            self.tools_definition = []
            self.base_system_prompt = ""
            self.message_history = []
            self.max_history_len = 1
            self.history_summary = ""
            logger.info("SimpleAgent initialized in non-grok mode for testing and data integration.")
        
        self.latest_game_state_text = "" # Stores text about last action & current state for the next user prompt

        self.app = app
        self.last_message = "" # For UI sidebar
        self._prev_sidebar_norm = ""
        self._step_count = 0 # Internal step counter
        self._initial_summary_done = False
        self._summary_every_n_steps = 15 # Configurable: summarize every N steps

        self.running = True # Agent control flag for the run loop
        self._last_assistant_message = ""  # Track last assistant message for get_last_message()

        # Simple action selection state for non-grok mode
        self._action_cycle_index = 0
        self._simple_actions = [0, 1, 2, 3, 4, 5]  # Basic movement and A/B button actions
        self._last_location = None
        self._stuck_counter = 0

    def get_frame_bytes(self) -> Optional[bytes]:
        screenshot_pil = self.reader.get_screenshot()
        if screenshot_pil:
            buffered = io.BytesIO()
            screenshot_pil.save(buffered, format="PNG")
            return buffered.getvalue()
        logger.warning("Could not get frame bytes, screenshot was None.")
        return None
    
    def get_last_message(self) -> str:
        """Return the last assistant message for agent_runner integration."""
        return self._last_assistant_message

    def _construct_user_message_content(self, text_prompt: str, image_base64: Optional[str]) -> List[Dict[str, Any]]:
        content: List[Dict[str, Any]] = [] # Ensure content is always a list
        if image_base64:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{image_base64}", "detail": "low"} # detail low for faster processing
            })
        content.append({"type": "text", "text": text_prompt})
        return content


    def process_tool_call(self, tool_name: str, tool_input: dict) -> Tuple[str, Dict[str, Any]]:
        """
        Process a tool function call.
        Note: This method is part of the old architecture where agent executed actions.
        In the new architecture, this should rarely be used since agent only decides actions.
        """
        logger.info(f"Attempting to execute tool: '{tool_name}' with input: {_pretty_json(tool_input)}")
        
        # Get the tool function implementation
        tool_function = next((tool_impl['function'] for tool_impl in AVAILABLE_TOOLS_LIST if tool_impl['function'].__name__ == tool_name), None)
        if not callable(tool_function):
            error_msg = f"Tool '{tool_name}' not found or its 'function' is not callable."
            logger.error(error_msg)
            self.latest_game_state_text = f"ERROR: Tool '{tool_name}' is not configured correctly."
            return error_msg, {"status": "error", "message": error_msg}

        human_readable_summary_from_tool: str
        structured_output_for_llm: Dict[str, Any]

        try:
            common_args = {
                'reader': self.reader,
                'quest_manager': self.quest_manager,
                'navigator': self.navigator,
                'env_wrapper': self.env_wrapper # Pass along if tools expect it
            }
            kwargs = {**common_args, **tool_input}
            
            human_readable_summary_from_tool, structured_output_for_llm = tool_function(**kwargs)
            logger.info(f"Tool '{tool_name}' executed. Human summary: '{human_readable_summary_from_tool}'. Structured output: {_pretty_json(structured_output_for_llm)}")

        except Exception as e:
            logger.error(f"Exception during execution of tool '{tool_name}': {e}", exc_info=True)
            error_msg = f"Error while running tool {tool_name}: {str(e)}"
            human_readable_summary_from_tool = error_msg # Pass the error as human summary
            structured_output_for_llm = {"status": "error", "message": error_msg}
        
        # Update latest game state text for UI (simplified, no direct reader calls)
        try:
            self.latest_game_state_text = (
                f"Last Action: {tool_name}({json.dumps(tool_input)})\n"
                f"Action Result: {human_readable_summary_from_tool}\n"
                f"Note: State details available from environment on next decision cycle."
            )
            logger.info(f"Updated self.latest_game_state_text based on '{tool_name}' outcome.")
        except Exception as e_state:
            logger.error(f"Failed to update latest_game_state_text after tool '{tool_name}': {e_state}", exc_info=True)
            self.latest_game_state_text = f"Error updating game state text after {tool_name}: {e_state}"

        # Return the direct results from the tool function call
        return human_readable_summary_from_tool, structured_output_for_llm

    def get_action(self, game_state: GameState) -> Optional[int]:
        """
        Get next action from Grok based on current game state.
        This is the main entry point for the agent - it only decides what to do,
        doesn't execute actions.
        
        Args:
            game_state: Current structured game state
            
        Returns:
            Action ID (0-7) or None if no action should be taken
        """
        if not self.grok_enabled:
            return None  # Let human control
            
        try:
            # Get current frame for visual context
            frame_bytes = None
            if self.env_wrapper:
                frame_array = self.env_wrapper.render()
                if frame_array is not None:
                    from PIL import Image
                    import io
                    frame_pil = Image.fromarray(frame_array)
                    img_byte_arr = io.BytesIO()
                    frame_pil.save(img_byte_arr, format='PNG')
                    frame_bytes = img_byte_arr.getvalue()
            
            # Use existing decision logic from step() method
            action = self._make_action_decision(frame_bytes, game_state)
            
            # Update latest message for UI
            self.latest_game_state_text = f"Decided on action: {action}"
            
            return action
            
        except Exception as e:
            logger.error(f"Error getting action from Grok: {e}")
            self.latest_game_state_text = f"Error in decision making: {e}"
            return None

    def _make_action_decision(self, frame_bytes, structured_state) -> Optional[int]:
        """
        Internal method that contains the actual grok decision logic.
        """
        if not self.grok_enabled:
            return None # Let human control
        
        try:
            # Build comprehensive game context for Grok
            context_parts = []
            
            # Add current game state information
            if structured_state:
                state_dict = asdict(structured_state)
                context_parts.append(f"Current Location: {state_dict.get('location', 'Unknown')}")
                context_parts.append(f"Coordinates: {state_dict.get('coords', [0,0,0])}")
                context_parts.append(f"Dialog: {state_dict.get('dialog', 'None')}")
                context_parts.append(f"HP: {state_dict.get('hp_fraction', 1.0)*100:.0f}%")
                context_parts.append(f"Badges: {state_dict.get('badges', 0)}")
                context_parts.append(f"Money: ${state_dict.get('money', 0)}")
                
                if state_dict.get('current_quest'):
                    context_parts.append(f"Current Quest: {state_dict['current_quest']}")
            
            # Build user prompt
            user_prompt = f"""
Pokemon Game State:
{chr(10).join(context_parts)}

Recent Action History:
{self.latest_game_state_text}

Analyze the current situation and decide the best action to take. Consider:
- If there's dialog, you should usually press A to continue
- For exploration, move strategically (UP/DOWN/LEFT/RIGHT)
- Use A to interact with NPCs, items, or confirmations
- Use B to cancel or go back
- Use START for the menu when needed
- Consider the current quest objective if available

What action should I take next?
"""

            # # Encode image if available
            # images = []
            # if frame_bytes:
            #     img_b64 = base64.b64encode(frame_bytes).decode('utf-8')
            #     images.append({
            #         "type": "image_url",
            #         "image_url": {"url": f"data:image/png;base64,{img_b64}"}
            #     })

            # Call Grok with the current state
            messages = [
                {"role": "system", "content": self.base_system_prompt},
                {
                    "role": "user", 
                    "content": [
                        {"type": "text", "text": user_prompt}
                    ] # + images
                }
            ]

            # Make API call to Grok
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                tools=self.tools_definition,
                temperature=self.temperature,
                max_tokens=self.max_tokens_completion
            )

            # Process response and extract action
            if response.choices[0].message.tool_calls:
                for tool_call in response.choices[0].message.tool_calls:
                    tool_name = tool_call.function.name
                    tool_args = json.loads(tool_call.function.arguments)
                    
                    # Convert tool call to action index
                    if tool_name == "press_button":
                        button = tool_args.get("button", "A")
                        action_index = self.quest_manager.action_to_index(button)
                        logger.info(f"Grok chose button: {button} (action {action_index})")
                        return action_index
                    elif tool_name == "press_next":
                        # Use PATH_FOLLOW_ACTION for quest progression
                        from environment.environment import PATH_FOLLOW_ACTION
                        logger.info(f"Grok chose path follow (action {PATH_FOLLOW_ACTION})")
                        return PATH_FOLLOW_ACTION
            
            # Fallback if no tool call
            logger.warning("Grok response had no tool calls, defaulting to A button")
            return self.quest_manager.action_to_index("A")
            
        except Exception as e:
            logger.error(f"Error in Grok decision making: {e}")
            # Simple fallback without direct reader calls
            return self.quest_manager.action_to_index("A")

