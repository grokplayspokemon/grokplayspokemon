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

# OpenAI SDK for xAI
from openai import OpenAI
from openai.types.chat import ChatCompletionMessage, ChatCompletionMessageToolCall # For type hinting

# Project-specific imports
from agent.prompts import SYSTEM_PROMPT, SUMMARY_PROMPT, INTROSPECTION_PROMPTS # Assuming these are still relevant and INTROSPECTION_PROMPTS exists
from agent.grok_tool_implementations import AVAILABLE_TOOLS_LIST
from environment.environment import RedGymEnv
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
        env_wrapper: EnvWrapper, # Made env_wrapper optional
        xai_api_key: Optional[str] = None, # Made optional, will check env var
        xai_base_url: str = "https://api.x.ai/v1",
        model_name: str = "grok-3-mini",
        reasoning_effort: str = "high", # "low" or "high", only for grok-3-mini models
        temperature: float = 0.7,
        max_tokens_completion: int = 1024,
        max_history_len: int = 20, # Max number of messages (turns) in history INCLUDING system prompt
        app=None # Optional: For UI updates or shared state
    ):
        assert reader is not None, "RedGymEnv (reader) must be provided"
        assert quest_manager is not None, "QuestManager must be provided"
        assert navigator is not None, "InteractiveNavigator must be provided"

        self.xai_api_key = xai_api_key or os.environ.get("XAI_API_KEY")
        if not self.xai_api_key:
            raise ValueError("xAI API key not provided and not found in XAI_API_KEY environment variable.")

        self.env_wrapper = env_wrapper # Store it, but primary interaction via reader
        self.reader = reader
        self.quest_manager = quest_manager
        self.navigator = navigator
        
        self.client = OpenAI(api_key=self.xai_api_key, base_url=xai_base_url)
        self.model_name = model_name
        # Validate reasoning_effort for grok-3-mini models
        if "grok-3-mini" in self.model_name:
            if reasoning_effort not in ["low", "high"]:
                logger.warning(f"Invalid reasoning_effort '{reasoning_effort}' for {self.model_name}. Defaulting to 'high'.")
                self.reasoning_effort = "high"
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
        self.latest_game_state_text = "" # Stores text about last action & current state for the next user prompt

        self.app = app
        self.last_message = "" # For UI sidebar
        self._prev_sidebar_norm = ""
        self._step_count = 0 # Internal step counter
        self._initial_summary_done = False
        self._summary_every_n_steps = 15 # Configurable: summarize every N steps

        self.running = True # Agent control flag for the run loop

        logger.info(f"SimpleAgent initialized for xAI. Model: {self.model_name}, Temp: {self.temperature}" +
                    (f", Reasoning: {self.reasoning_effort}" if self.reasoning_effort else ""))
        logger.info(f"Tools loaded: {[tool['function']['name'] for tool in self.tools_definition]}") # Corrected to use function name

    def get_frame_bytes(self) -> Optional[bytes]:
        screenshot_pil = self.reader.get_screenshot()
        if screenshot_pil:
            buffered = io.BytesIO()
            screenshot_pil.save(buffered, format="PNG")
            return buffered.getvalue()
        logger.warning("Could not get frame bytes, screenshot was None.")
        return None

    def _construct_user_message_content(self, text_prompt: str, image_base64: Optional[str]) -> List[Dict[str, Any]]:
        content: List[Dict[str, Any]] = [] # Ensure content is always a list
        if image_base64:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{image_base64}", "detail": "low"} # detail low for faster processing
            })
        content.append({"type": "text", "text": text_prompt})
        return content

    def _normalize_location(self, loc: str) -> str:
        if not loc: return "Unknown Location"
        return loc.strip().title()

    def process_tool_call(self, tool_name: str, tool_input: dict) -> Tuple[str, Dict[str, Any]]:
        logger.info(f"Attempting to execute tool: '{tool_name}' with input: {_pretty_json(tool_input)}")
        
        tool_function = None
        for tool_data in AVAILABLE_TOOLS_LIST:
            if tool_data['declaration']['function']['name'] == tool_name:
                tool_function = tool_data.get('function')
                break
        
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
        
        # Regardless of tool success or failure, capture the game state *after* the attempt.
        # This state will be used for the self.latest_game_state_text for the *next* user prompt.
        try:
            current_dialog_after_tool = _clean_dialog(self.reader.read_dialog())
            player_x_after_tool, player_y_after_tool, map_id_after_tool = self.reader.get_game_coords()
            location_name_after_tool = self._normalize_location(self.reader.get_map_name_by_id(map_id_after_tool))
            
            game_state_obj_after_tool = extract_structured_game_state(self.env_wrapper, self.reader, self.quest_manager)
            # Create a concise string representation of this game state object for the prompt
            gs_dict_after_tool = asdict(game_state_obj_after_tool)
            concise_state_str = (
                f"Location: {location_name_after_tool} (X: {player_x_after_tool}, Y: {player_y_after_tool}, MapID: {map_id_after_tool})\n"
                f"Dialog: {current_dialog_after_tool}\n"
                f"Party HP: {gs_dict_after_tool.get('hp_fraction', 1.0)*100:.0f}% | Badges: {gs_dict_after_tool.get('badges',0)} | Money: ${gs_dict_after_tool.get('money',0)}"
            )
            exploration_log = self.navigator.get_exploration_log(limit=3) # Brief log
            exploration_log_str = "\n".join([f"- {entry['timestamp']}: {entry['description']}" for entry in exploration_log])
            if not exploration_log_str: exploration_log_str = "No recent exploration notes."

            self.latest_game_state_text = (
                f"Last Action: {tool_name}({json.dumps(tool_input)})\n"
                f"Action Result: {human_readable_summary_from_tool}\n\n"
                f"Game State After Action:\n{concise_state_str}\n\n"
                f"Recent Exploration:\n{exploration_log_str}"
            )
            logger.info(f"Updated self.latest_game_state_text based on '{tool_name}' outcome.")
        except Exception as e_state:
            logger.error(f"Failed to update latest_game_state_text after tool '{tool_name}': {e_state}", exc_info=True)
            self.latest_game_state_text = f"Error updating game state text after {tool_name}: {e_state}"

        # Return the direct results from the tool function call
        return human_readable_summary_from_tool, structured_output_for_llm

    def step(self):
        try:
            # 1. Prepare User Message (Text + Optional Image)
            prompt_text = "Observe the game state and decide the next action or tool call.\n"
            if self.latest_game_state_text:
                prompt_text += "\nPREVIOUS ACTION & RESULTING STATE:\n" + self.latest_game_state_text
            else:
                x, y, current_map_id = self.reader.get_game_coords()
                loc = self._normalize_location(self.reader.get_map_name_by_id(current_map_id))
                dialog = _clean_dialog(self.reader.read_dialog())
                prompt_text += f"\nCURRENT GAME STATE:\nLocation: {loc} (X: {x}, Y: {y}, MapID: {current_map_id})\nDialog: {dialog}"
            
            self._step_count += 1
            if self._step_count > 1 and self._step_count % 7 == 0: 
                prompt_text += "\n\nREFLECTION QUESTION:\n" + random.choice(INTROSPECTION_PROMPTS)

            img_b64 = get_screenshot_base64(self.reader.get_screenshot())
            user_msg_content = self._construct_user_message_content(prompt_text, img_b64)
            self.message_history.append({"role": "user", "content": user_msg_content})
            self._trim_history()

            # 2. First LLM Call
            logger.info(f"Sending request to xAI model ({self.model_name}). History: {len(self.message_history)} items.")
            api_params = {
                "model": self.model_name,
                "messages": self.message_history,
                "tools": self.tools_definition,
                "tool_choice": "auto",
                "temperature": self.temperature,
                "max_tokens": self.max_tokens_completion
            }
            if self.reasoning_effort:
                api_params["reasoning_effort"] = self.reasoning_effort
            
            completion = self.client.chat.completions.create(**api_params)
            
            if hasattr(completion.choices[0].message, 'reasoning_content') and completion.choices[0].message.reasoning_content:
                logger.info(f"xAI Reasoning: {completion.choices[0].message.reasoning_content}")
            if completion.usage: logger.info(f"xAI Usage (1st call): {_pretty_json(completion.usage.model_dump())}")

            assistant_msg: ChatCompletionMessage = completion.choices[0].message
            self.message_history.append(assistant_msg.model_dump()) # model_dump() gives dict
            self._update_sidebar(assistant_msg.content if assistant_msg.content else "(Tool call requested by LLM)")
            self._trim_history()

            # 3. Process Tool Calls if any
            if assistant_msg.tool_calls:
                tool_call_results_for_history: List[Dict[str,Any]] = []
                for tool_call in assistant_msg.tool_calls:
                    tc_id = tool_call.id
                    func_name = tool_call.function.name
                    logger.info(f"LLM requested tool: {func_name}, ID: {tc_id}")
                    try:
                        func_args = json.loads(tool_call.function.arguments)
                    except json.JSONDecodeError as e:
                        logger.error(f"JSONDecodeError for tool {func_name} args '{tool_call.function.arguments}': {e}")
                        # process_tool_call will handle this error internally by returning an error structure
                        _, structured_err_dict = self.process_tool_call(func_name, {"_parse_error": str(e)}) # Pass error info
                        tool_result_content_str = json.dumps(structured_err_dict)
                    else:
                        # process_tool_call updates self.latest_game_state_text
                        _, structured_result_dict = self.process_tool_call(func_name, func_args)
                        tool_result_content_str = json.dumps(structured_result_dict)
                    
                    tool_call_results_for_history.append({
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "name": func_name,
                        "content": tool_result_content_str
                    })
                
                self.message_history.extend(tool_call_results_for_history)
                self._trim_history()

                # 4. Second LLM Call with tool results
                logger.info("Sending 2nd request to xAI model with tool results.")
                api_params_final = {
                    "model": self.model_name,
                    "messages": self.message_history,
                    "tools": self.tools_definition,
                    "tool_choice": "none", # Expect natural language
                    "temperature": self.temperature,
                    "max_tokens": self.max_tokens_completion
                }
                # Reasoning usually not for this summarization step
                # if self.reasoning_effort: api_params_final["reasoning_effort"] = self.reasoning_effort 

                final_completion = self.client.chat.completions.create(**api_params_final)
                if hasattr(final_completion.choices[0].message, 'reasoning_content') and final_completion.choices[0].message.reasoning_content:
                    logger.info(f"xAI Reasoning (final): {final_completion.choices[0].message.reasoning_content}")
                if final_completion.usage: logger.info(f"xAI Usage (2nd call): {_pretty_json(final_completion.usage.model_dump())}")
                
                final_assistant_text = final_completion.choices[0].message.content or "(No text in final LLM response)"
                self.message_history.append(final_completion.choices[0].message.model_dump())
                self._update_sidebar(final_assistant_text)
                logger.info(f"xAI Final Response: {final_assistant_text}")
                self.latest_game_state_text = "" # Clear as it's incorporated
            else:
                # No tool calls, the first assistant message was the final one
                final_text = assistant_msg.content or "(No text content from LLM)"
                logger.info(f"xAI Response (no tool call): {final_text}")
                self.latest_game_state_text = "" # Clear as it's incorporated
            
            # 5. Periodic Summarization
            if not self._initial_summary_done and self._step_count >= 5:
                self.summarize_history()
                self._initial_summary_done = True
            elif self._step_count > 0 and self._step_count % self._summary_every_n_steps == 0:
                self.summarize_history()
            
            self._trim_history() # Final trim after all appends

        except Exception as e:
            logger.error(f"Error in SimpleAgent step: {e}", exc_info=True)
            self.latest_game_state_text = f"Agent critical error: {e}" 

    def summarize_history(self):
        if len(self.message_history) < 3: 
            logger.info("History too short to summarize.")
            return

        logger.info("Summarizing conversation history...")
        
        # Provide current game state as context for the summarizer
        try:
            current_dialog_for_summary = _clean_dialog(self.reader.read_dialog())
            player_x_for_summary, player_y_for_summary, map_id_for_summary = self.reader.get_game_coords()
            location_name_for_summary = self._normalize_location(self.reader.get_map_name_by_id(map_id_for_summary))
            gs_obj_for_summary = extract_structured_game_state(self.env_wrapper, self.reader, self.quest_manager)
            gs_dict_for_summary = asdict(gs_obj_for_summary)
            summary_context_text = (
                f"Current Game State Context for Summarization:\n"
                f"Location: {location_name_for_summary} (X: {player_x_for_summary}, Y: {player_y_for_summary}, MapID: {map_id_for_summary})\n"
                f"Dialog: {current_dialog_for_summary}\n"
                f"Party HP: {gs_dict_for_summary.get('hp_fraction', 1.0)*100:.0f}% | Badges: {gs_dict_for_summary.get('badges',0)} | Money: ${gs_dict_for_summary.get('money',0)}"
            )
        except Exception as e_state_summary:
            logger.error(f"Failed to get game state for summary context: {e_state_summary}")
            summary_context_text = "Could not retrieve current game state for summary context due to an error."

        # Filter out image messages for summary prompt to save tokens and reduce complexity for summarizer
        history_for_summary_prompt = []
        for msg in self.message_history:
            if msg["role"] == "user" and isinstance(msg["content"], list):
                text_parts = [item["text"] for item in msg["content"] if item["type"] == "text"]
                if text_parts:
                    history_for_summary_prompt.append({"role": "user", "content": "\n".join(text_parts)})
            elif msg["role"] != "system": # Keep assistant and tool messages as is, skip system prompt
                history_for_summary_prompt.append(msg)

        summary_user_prompt = (
            f"{summary_context_text}\n\n"
            f"Please provide a concise summary of the preceding conversation, focusing on key decisions, outcomes, and current objectives. "
            f"This summary will be used to give context to a game-playing agent in future turns. "
            f"Retain critical game state information and strategic shifts mentioned in the dialogue. Do not repeat tool call arguments verbatim unless essential for context."
        )
        
        summary_messages = [
            {"role": "system", "content": SUMMARY_PROMPT}, # Use the imported SUMMARY_PROMPT
            *history_for_summary_prompt, 
            {"role": "user", "content": summary_user_prompt}
        ]

        try:
            # Can use a different model or settings for summarization if desired
            summary_api_params: Dict[str, Any] = {
                "model": self.model_name, # Or a model better suited for summarization
                "messages": summary_messages,
                "temperature": 0.3, # Lower temp for more factual summary
                "max_tokens": 700, # Adjust as needed for summary length
                # "reasoning_effort": "low" # Optional: reasoning might not be needed or beneficial for summarization
            }
            if "grok-3-mini" in summary_api_params["model"] and self.reasoning_effort: # Only if model supports it
                 summary_api_params["reasoning_effort"] = "low" # Use low for summarization
            
            completion = self.client.chat.completions.create(**summary_api_params)

            summary_text = completion.choices[0].message.content
            if summary_text:
                self.history_summary = summary_text.strip()
                logger.info(f"History summarized. New summary length: {len(self.history_summary)} chars.")
                # logger.debug(f"Full summary: {self.history_summary}")
                
                # Update the system prompt content in the current history
                if self.message_history and self.message_history[0]["role"] == "system":
                    self.message_history[0]["content"] = self._get_current_system_prompt_content()
                else: # Should not happen if history is always initialized with system prompt
                    self.message_history.insert(0, {"role": "system", "content": self._get_current_system_prompt_content()})
                
                # Now, trim non-system messages, keeping only the most recent ones if needed after summary
                # For example, keep last 2-3 turns to ensure smooth transition.
                # For now, strict summarization will just keep the system prompt + summary.
                # If we want to keep a few recent messages: Find first non-system message after system prompt.
                # Keep N messages from the end of `history_for_summary_prompt` (which excludes system prompt).
                # For now, let _trim_history manage based on max_history_len and the (now updated) system prompt.
                # The goal is that the *next* _trim_history call will correctly shorten based on max_history_len.
                # No, we need to actively shorten it here to effectively replace the history.
                
                # Replace message history with system prompt (containing summary) and optionally a few recent messages.
                # For the strict approach: only the system prompt with summary remains.
                # For a softer approach, we could append the last few user/assistant/tool messages.
                # Let's go with a strict approach for now: the summary *is* the history for the system prompt.
                # Then, subsequent turns build upon this.
                # This requires _trim_history to be robust.

                # Let's adjust: the summary updates the system prompt. We don't discard the *entire* history immediately,
                # but _trim_history will eventually prune older messages naturally. The summary just helps make the system prompt richer.
                # The user might want to see old history if they scroll back.
                # The *effective* history for the LLM is now shorter due to the summary being in the system prompt.
            else:
                logger.warning("Summarization attempt yielded empty content.")

        except Exception as e:
            logger.error(f"Error during history summarization: {e}", exc_info=True)

    def run(self, num_steps=1):
        logger.info(f"SimpleAgent run loop starting for {num_steps} steps.")
        steps_taken = 0
        while self.running and steps_taken < num_steps:
            if self.app and hasattr(self.app, 'state') and hasattr(self.app.state, 'is_paused') and self.app.state.is_paused:
                try:
                    time.sleep(0.1)
                except AttributeError:
                    # time might not be imported if this is run outside __main__ guard
                    pass 
                continue
            
            logger.info(f"--- Agent Step {self._step_count +1} --- (Run {steps_taken + 1}/{num_steps})")
            self.step() 
            steps_taken += 1
            
            if not self.running:
                logger.info("Agent run loop interrupted.")
                break
        logger.info(f"SimpleAgent run loop finished after {steps_taken} steps.")

    def stop(self):
        logger.info("Stopping SimpleAgent...")
        self.running = False
        # if hasattr(self.env_wrapper, 'stop') and callable(self.env_wrapper.stop):
        # self.env_wrapper.stop()

if __name__ == '__main__':
    # This __main__ block is for basic testing.
    # HOOK IT UP TO THE REAL ENVIRONMENT!

    from environment.environment import RedGymEnv
    env = RedGymEnv()
    
    # Do not mock components!!! We have all the things we need!! Call the right functions!!
    # MAJOR TODO: USE REAL FUNCTIONS! NO MOCKS!!!
    class MockEnvWrapper:
        def get_screenshot(self): return env.get_screenshot()
        def get_location_name(self): return "Pallet Town - Test"
        def stop(self): logger.info("MockEnvWrapper stopped.")

    class MockReader:
        def get_player_coordinates(self): return (10, 11)
        def get_current_dialog(self): return "Test dialog!"
        def get_location_name(self): return "Pallet Town - Test"
        def read_party_pokemon(self): return []
        def read_money(self): return 1234
        def read_badges(self): return ["BOULDERBADGE"]
        def read_pokedex_seen_count(self): return 5
        def read_pokedex_caught_count(self): return 2
        def read_items(self): return [("POTION", 5)]
        
    class MockQuestManager:
        def get_current_quest(self): return None

    class MockNavigator:
        def get_exploration_log(self, limit=5): return [{"timestamp": "now", "description": "explored mock area"}]
        def navigate_to_coords(self, y, x, max_steps): return False, "Mock navigation to coords failed."
        def move_in_direction(self, direction, steps): return False, "Mock directional move failed."

    # Agent Prompts (minimal for testing)
    SYSTEM_PROMPT = "You are a helpful Pokemon game playing AI. Your goal is to explore and complete objectives."
    SUMMARY_PROMPT = "Please summarize the game session concisely."
    INTROSPECTION_PROMPTS = ["What is the most important thing to do right now?"]
    
    # Tools (minimal for testing - real tools are in agent.tools)
    # In a real run, AVAILABLE_TOOLS_LIST would be imported from agent.tools
    # For this standalone test, if SimpleAgent needs it populated, mock it or ensure agent.tools is importable.
    # For now, assuming SimpleAgent will load its tools from the imported AVAILABLE_TOOLS_LIST.
    # If agent.tools is not available in this test scope, this will fail or tools_definition will be empty.
    try:
        from agent.tools import AVAILABLE_TOOLS_LIST
    except ImportError:
        logger.error("Could not import AVAILABLE_TOOLS_LIST from agent.tools for __main__ test. Tool functionality will be limited.")
        AVAILABLE_TOOLS_LIST = [] # Fallback to empty list

    xai_api_key_from_env = os.environ.get("XAI_API_KEY")
    if not xai_api_key_from_env:
        print("ERROR: XAI_API_KEY environment variable not set. This test requires it.")
        exit(1)

    logger.info("Starting SimpleAgent __main__ test with mocked components...")
    
    agent = SimpleAgent(
        reader=env,
        quest_manager=MockQuestManager(),
        navigator=MockNavigator(),
        env_wrapper=None, # Explicitly None, as reader is RedGymEnv
        xai_api_key=xai_api_key_from_env,
        model_name="grok-3-mini", # or your preferred test model
        reasoning_effort="low" # Use low for faster test responses
    )

    # Replace placeholder methods with more interactive ones for testing if desired
    # For now, the placeholder step() will run.
    try:
        agent.run(num_steps=3) # Run a few placeholder steps
    except Exception as e:
        logger.error(f"Agent run failed during __main__ test: {e}", exc_info=True)
    finally:
        agent.stop()
    logger.info("SimpleAgent __main__ test finished.")