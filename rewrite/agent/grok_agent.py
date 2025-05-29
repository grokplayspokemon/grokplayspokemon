import os
import json
from openai import OpenAI

# Tool definitions
from tools.press_next import press_next_tool
from tools.press_button import press_button_tool
# Configuration helpers
from tools.model_config import get_model_name, get_temperature, get_top_p
# Reasoning helpers
from tools.reasoning import get_reasoning_effort, extract_reasoning
# Usage metrics helpers
from tools.metrics import extract_usage_metrics
# Retry decorator for API calls
from tools.retry import retry_on_exception

# Logging setup
from agent_logging.logger import get_logger
logger = get_logger(__name__)

# Validator
from validator import validate_messages, validate_tools, validate_function_call

class GrokAgent:
    """
    Synchronous AI agent for turn-by-turn control.  
    Implements initialize, get_action, and on_feedback hooks.
    """
    def __init__(self):
        self.env = None
        self.navigator = None
        self.quest_manager = None
        self.model = None
        self.client = None
        # Last API reasoning trace and usage stats
        self.last_reasoning = None
        self.last_usage = None

    def initialize(self, env, navigator, quest_manager):  # noqa: F841
        """
        Store references to environment, navigator, and quest manager.
        Initialize OpenAI API key and model name.
        """
        logger.info("initialize called with env=%s, navigator=%s, quest_manager=%s", env, navigator, quest_manager)
        self.env = env
        self.navigator = navigator
        self.quest_manager = quest_manager
        # Initialize XAI client
        api_key = os.getenv("XAI_API_KEY", os.getenv("OPENAI_API_KEY"))
        base_url = os.getenv("XAI_BASE_URL", "https://api.x.ai/v1")
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        # Determine model name via config helper or environment
        self.model = get_model_name()

    def get_action(self, state: dict) -> dict:
        """
        Synchronously query the xAI model for exactly one tool call.
        Each turn, we send the full game state and available tools,
        and receive back a `function_call` indicating which tool to invoke.
        Returns a dict: {"name": tool_name, "args": {...}}.
        """
        logger.info("get_action called with state: %s", state)
        system_prompt = (
            "You are Grok, an AI agent playing Pokémon Red. "
            "Each turn you receive the full game state as JSON. "
            "You must respond with exactly one JSON object indicating the tool call to make. "
            "Available tool calls: press_next (no arguments); "
            "press_button with args {\"button\":\"UP\"|\"DOWN\"|\"LEFT\"|\"RIGHT\"|\"A\"|\"B\"|\"START\"}. "
            "Do not include any other text or explanation."
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(state)}
        ]
        # List of available tools for function calling
        tools = [press_next_tool, press_button_tool]
        # Synchronous API call with retry for transient errors
        response = self._call_api(messages, tools)
        msg = response.choices[0].message
        # Extract the single function_call from the response
        func_call = None
        if hasattr(msg, 'function_call') and msg.function_call:
            func_call = msg.function_call
        elif hasattr(msg, 'tool_calls') and msg.tool_calls:
            # xAI may return a list of tool_calls; take the first
            func_call = msg.tool_calls[0].function
        else:
            raise ValueError(f"No function_call found in response: {msg}")
        name = func_call.name
        # Arguments may be a JSON string; parse into dict
        raw_args = func_call.arguments or '{}'
        try:
            args = json.loads(raw_args)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in function_call arguments: {raw_args}") from e
        # Validate the extracted function call
        validate_function_call({"name": name, "arguments": args})
        # Save reasoning and usage for UI/metrics
        self.last_reasoning = extract_reasoning(response)
        self.last_usage = extract_usage_metrics(response)
        logger.info("get_action result: name=%s, args=%s", name, args)
        logger.info("last_reasoning: %s", self.last_reasoning)
        logger.info("last_usage: %s", self.last_usage)
        return {"name": name, "args": args}

    def on_feedback(self, state: dict, action: dict, reward: float, info: dict):
        """
        Receive feedback after each step; no-op for now.
        """
        logger.info("on_feedback called with state=%s, action=%s, reward=%s, info=%s", state, action, reward, info)
        pass

    @retry_on_exception()
    def _call_api(self, messages, tools):  # noqa: C901
        """
        Internal helper to call the xAI chat completion endpoint with retry.
        Automatically includes model, messages, tools, and sampling parameters.
        """
        # Schema validation before API call
        validate_messages(messages)
        validate_tools(tools)
        # Gather parameters for request
        effort = get_reasoning_effort()
        temp = get_temperature()
        top_p_val = get_top_p()
        # Log full API request details for debugging
        logger.info(
            "API request: model=%s, messages=%s, tools=%s, reasoning_effort=%s, temperature=%s, top_p=%s",
            self.model,
            messages,
            tools,
            effort,
            temp,
            top_p_val,
        )
        # Perform the API call
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            reasoning_effort=effort,
            temperature=temp,
            top_p=top_p_val,
        )
        # Log raw API response for debugging
        logger.info("API raw response: %s", response)
        return response 