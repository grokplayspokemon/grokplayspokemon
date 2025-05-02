import base64
import copy
import io
import json
import logging
import os
import requests
import time

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

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s') # Level might be set higher up now
logger = logging.getLogger(__name__)


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
        logger.info(f"Processing tool call: {tool_name}")

        if tool_name == "press_buttons":
            buttons = tool_input["buttons"]
            wait = tool_input.get("wait", True)
            logger.info(f"[Buttons] Pressing: {buttons} (wait={wait})")
            
            result = self.emulator.press_buttons(buttons, wait)
            
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
        try:
            # Prepare message history and include latest memory state for context
            messages = copy.deepcopy(self.message_history)
            try:
                memory_info = self.emulator.get_state_from_memory()
                # Append current memory state as a user message
                messages.append({"role": "user", "content": f"Memory State:\n{memory_info}"})
            except Exception as e:
                logger.warning(f"Failed to read memory for context: {e}")

            if len(messages) >= 3:
                if messages[-1]["role"] == "user" and isinstance(messages[-1]["content"], list) and messages[-1]["content"]:
                    if messages[-1]["content"][-1].get("type") == "tool_result":
                        screenshot = self.emulator.get_screenshot_with_overlay() if self.use_overlay else self.emulator.get_screenshot()
                        screenshot_b64 = get_screenshot_base64(screenshot, upscale=self.screenshot_upscale) # Use config upscale
                        collision_map = self.emulator.get_collision_map()
                        messages[-1]["content"].append(
                            {"type": "text", "text": "\nHere is a screenshot of the current screen:"}
                        )
                        messages[-1]["content"].append(
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": screenshot_b64,
                                },
                            }
                        )
                        if collision_map:
                            messages[-1]["content"].append(
                                {"type": "text", "text": f"\nCollision Map:\n{collision_map}"}
                            )
            else:
                screenshot = self.emulator.get_screenshot_with_overlay() if self.use_overlay else self.emulator.get_screenshot()
                screenshot_b64 = get_screenshot_base64(screenshot, upscale=self.screenshot_upscale) # Use config upscale
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

            # --- Call LLM based on provider ---
            if self.provider == 'anthropic':
                # Anthropic API call
                response = self.anthropic_client.messages.create(
                    model=self.model_name,
                    max_tokens=self.max_tokens, # Use config value
                    temperature=self.temperature, # Use config value
                    system=self.system_prompt,
                    messages=messages,
                    tools=AVAILABLE_TOOLS,
                    tool_choice={"type": "auto"},
                )
                # ... (rest of Anthropic response handling)
            elif self.provider == 'openai':
                 # OpenAI API call
                 try:
                     # Map tools to OpenAI format if necessary
                     openai_tools = []
                     for tool in AVAILABLE_TOOLS:
                         openai_tools.append({"type": "function", "function": tool})

                     # Map message format
                     openai_messages = []
                     for msg in messages:
                         if msg["role"] == "user":
                             # Handle complex content (text + image)
                             if isinstance(msg["content"], list):
                                 new_content = []
                                 for item in msg["content"]:
                                     if isinstance(item, dict):
                                         if item.get("type") == "text":
                                             new_content.append({"type": "text", "text": item["text"]})
                                         elif item.get("type") == "image":
                                             # Convert base64 image source for OpenAI
                                             img_data = item["source"]["data"]
                                             media_type = item["source"]["media_type"]
                                             new_content.append({"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{img_data}"}})
                                 openai_messages.append({"role": "user", "content": new_content})
                             else:
                                # Simple text content
                                openai_messages.append({"role": "user", "content": msg["content"]})
                         elif msg["role"] == "assistant":
                             # Check for tool calls in assistant message
                             tool_calls = []
                             content = msg.get("content", "")
                             if msg.get("tool_calls"): 
                                 for tc in msg["tool_calls"]:
                                     tool_calls.append({"id": tc["id"], "type": "function", "function": {"name": tc["name"], "arguments": json.dumps(tc["input"])}})
                             openai_messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls if tool_calls else None})
                         elif msg["role"] == "tool": # Role used by Anthropic for tool results
                             # Map tool result back for OpenAI
                             openai_messages.append({"role": "tool", "tool_call_id": msg["tool_use_id"], "content": json.dumps(msg["content"])})
                             
                     response = self.openai_client.chat.completions.create(
                         model=self.model_name,
                         messages=openai_messages,
                         tools=openai_tools,
                         tool_choice="auto",
                         max_tokens=self.max_tokens, # Use config value
                         temperature=self.temperature # Use config value
                     )
                     
                     # Process OpenAI response
                     response_message = response.choices[0].message
                     text_content = response_message.content or ""
                     tool_calls = []
                     
                     if response_message.tool_calls:
                         logger.info(f"OpenAI response has tool calls: {response_message.tool_calls}")
                         for tc in response_message.tool_calls:
                             # Convert back to Anthropic-like format for processing
                             tool_calls.append(
                                 {
                                     "id": tc.id,
                                     "type": "function", # Match Anthropic type key
                                     "name": tc.function.name,
                                     "input": json.loads(tc.function.arguments),
                                 }
                             )
                     
                     # Update last message and history
                     self.last_message = text_content or "No text response"
                     logger.info(f"[Text] {self.last_message}")
                     assistant_message = {"role": "assistant", "content": text_content, "tool_calls": tool_calls if tool_calls else []}
                     self.message_history.append(assistant_message)
                     
                     # Process tool calls if any
                     if tool_calls:
                         tool_results = []
                         for tool_call in tool_calls:
                             # Re-wrap tool_call to match expected structure for process_tool_call
                             class FakeToolCall:
                                 def __init__(self, data):
                                     self.name = data["name"]
                                     self.input = data["input"]
                                     self.id = data["id"]
                                     self.type = data["type"]
                                     
                             result = self.process_tool_call(FakeToolCall(tool_call))
                             tool_results.append(result)
                             
                         self.message_history.append({"role": "user", "content": tool_results})
                     
                 except Exception as e:
                     logger.error(f"Error calling OpenAI API: {e}")
                     self.last_message = f"Error: Could not get response from OpenAI: {e}"
                 
            elif self.provider == 'grok':
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